"""SICOP Opportunity Hub — paginated SICOP import and searchable database."""
import csv
import io
import json
import math
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, Response, flash, jsonify, redirect, render_template,
                   request, session, stream_with_context, url_for)
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app.sicop_source import (SICOPSourceError, get_page, make_session, normalize,
                              canonical_procedure_number)
from app.technology import (TECH_FILTER_OPTIONS, matched_areas,
                            selected_filter, classified_sql, classify_opportunity)
from app.presentation import buyer_name, clean_description, brief_description
from app.publication import date_from_row, display_publication_date, parse_publication_date
from app.technology import AREA_LABELS, CLASSIFICATION_VERSION
from app.catalog_sql import CATALOG_FROM
from app.soc_radar import analyze_soc
from app.incremental import recent_months, due_for_refresh
from app.observatorio import (ObservatoryImportError, download_archive,
                             iter_carteles, validate_month, official_archive_url)
from pathlib import Path

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'development-only-change-me')
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CSV_UPLOAD_MB', '70')) * 1024 * 1024

# The free Render web filesystem is ephemeral. Never silently store production
# data in /tmp/sicop.db when DATABASE_URL is missing.
PERSISTENCE_REQUIRED = (os.getenv('REQUIRE_PERSISTENT_DB', '').lower() in ('1','true','yes')
                        or bool(os.getenv('RENDER_SERVICE_ID')) or os.getenv('RENDER') == 'true')
if PERSISTENCE_REQUIRED and not os.getenv('DATABASE_URL'):
    raise RuntimeError('DATABASE_URL no está configurada. Render necesita PostgreSQL persistente; se bloqueó SQLite temporal para evitar pérdida de datos.')
DB_URL = os.getenv('DATABASE_URL', 'sqlite:////tmp/sicop.db')
if PERSISTENCE_REQUIRED and not DB_URL.startswith(('postgres://','postgresql://','postgresql+psycopg://')):
    raise RuntimeError('Base de datos no persistente en Render. Configurá DATABASE_URL de PostgreSQL antes de desplegar.')
if DB_URL.startswith('postgres://'):
    DB_URL = DB_URL.replace('postgres://', 'postgresql+psycopg://', 1)
elif DB_URL.startswith('postgresql://'):
    DB_URL = DB_URL.replace('postgresql://', 'postgresql+psycopg://', 1)
engine = create_engine(DB_URL, pool_pre_ping=True, pool_recycle=300)
SICOP_OPEN_DATA_URL = os.getenv('SICOP_OPEN_DATA_URL',
                               'https://www.sicop.go.cr/moduloBid/report/RE_DatosAbiertosConcursosView.jsp')
# Multiple calls are needed for large results. Keep each HTTP request within Render's timeout.
PAGES_PER_RUN = max(1, min(100, int(os.getenv('SICOP_PAGES_PER_RUN', '20'))))
MAX_RUN_SECONDS = max(20, min(145, int(os.getenv('SICOP_SYNC_MAX_SECONDS', '95'))))
PAGE_DELAY_SECONDS = max(0, min(5, float(os.getenv('SICOP_PAGE_DELAY_SECONDS', '0.5'))))

LEGACY_BOOTSTRAP_USERS = {
    'juanjose.solanocamacho@noventiq.com': ('admin', 'ADMIN_PASSWORD_1'),
    'jjckb@gmail.com': ('admin', 'ADMIN_PASSWORD_2'),
    'alonso.fallasvargas@noventiq.com': ('user', 'USER_PASSWORD_1'),
    'diana.diaz@noventiq.com': ('user', 'USER_PASSWORD_1'),
    'juan.marincabezas@noventiq.com': ('user', 'USER_PASSWORD_1'),
    'a.wong@noventiq.com': ('user', 'USER_PASSWORD_1'),
    'ziomara.chavarria@noventiq.com': ('user', 'USER_PASSWORD_1'),
}

DDL = [
    '''CREATE TABLE IF NOT EXISTS users(email VARCHAR(255) PRIMARY KEY,
       password_hash TEXT NOT NULL,role VARCHAR(20) NOT NULL,updated_at TEXT NOT NULL,
       is_active INTEGER NOT NULL DEFAULT 1,auth_version INTEGER NOT NULL DEFAULT 1,
       created_at TEXT,last_login TEXT)''',
    '''CREATE TABLE IF NOT EXISTS app_settings(key VARCHAR(120) PRIMARY KEY, value TEXT)''',
    '''CREATE TABLE IF NOT EXISTS tenders(id VARCHAR(220) PRIMARY KEY,
       description TEXT,procedure_number VARCHAR(140),institution TEXT,
       procedure_type TEXT,modality TEXT,object_classification TEXT,item TEXT,
       line_number TEXT,awardee TEXT,currency VARCHAR(20),amount DOUBLE PRECISION,
       exchange_rate DOUBLE PRECISION,awarded_crc DOUBLE PRECISION,status VARCHAR(180),
       source_url TEXT,first_seen TEXT,last_seen TEXT, publication_date VARCHAR(10),
       is_it INTEGER,it_score INTEGER,it_priority VARCHAR(20),it_labels TEXT,
       classification_version INTEGER)''',
    '''CREATE TABLE IF NOT EXISTS observatory_tenders(
       procedure_number VARCHAR(140) PRIMARY KEY,description TEXT,institution TEXT,
       procedure_type TEXT,modality TEXT,object_classification TEXT,
       publication_date VARCHAR(10),closing_date VARCHAR(10),status VARCHAR(180),
       currency VARCHAR(20),amount DOUBLE PRECISION,source_url TEXT,
       archive_month VARCHAR(6),first_seen TEXT,last_seen TEXT,
       is_it INTEGER,it_score INTEGER,it_priority VARCHAR(20),it_labels TEXT,
       classification_version INTEGER)''',
    '''CREATE TABLE IF NOT EXISTS observatory_imports(
       month VARCHAR(6) PRIMARY KEY,imported_at TEXT,records INTEGER,
       status VARCHAR(40),message TEXT,created INTEGER DEFAULT 0,updated INTEGER DEFAULT 0,unchanged INTEGER DEFAULT 0)''',
    '''CREATE INDEX IF NOT EXISTS ix_obs_date ON observatory_tenders(publication_date)''',
    '''CREATE INDEX IF NOT EXISTS ix_obs_it ON observatory_tenders(is_it,it_score)''',
    '''CREATE TABLE IF NOT EXISTS sync_runs(id BIGSERIAL PRIMARY KEY,started_at TEXT,
       finished_at TEXT,status VARCHAR(40),imported INTEGER,message TEXT)''',
    '''CREATE TABLE IF NOT EXISTS sync_state(id INTEGER PRIMARY KEY,next_page INTEGER NOT NULL,
       total_pages INTEGER,total_results INTEGER,pages_done INTEGER NOT NULL DEFAULT 0,
       last_signature TEXT,lock_id TEXT,locked_until TEXT,updated_at TEXT)''',
    '''CREATE INDEX IF NOT EXISTS ix_tenders_procedure ON tenders(procedure_number)''',
    '''CREATE INDEX IF NOT EXISTS ix_tenders_status ON tenders(status)''',
]

UPSERT = text('''INSERT INTO tenders(id,description,procedure_number,institution,
    procedure_type,modality,object_classification,item,line_number,awardee,currency,
    amount,exchange_rate,awarded_crc,status,source_url,first_seen,last_seen,
    is_it,it_score,it_priority,it_labels,publication_date,classification_version)
    VALUES(:id,:description,:procedure_number,:institution,:procedure_type,:modality,
    :object_classification,:item,:line_number,:awardee,:currency,:amount,:exchange_rate,
    :awarded_crc,:status,:source_url,:first_seen,:last_seen,
    :is_it,:it_score,:it_priority,:it_labels,:publication_date,:classification_version)
    ON CONFLICT(id) DO UPDATE SET
    description=CASE WHEN excluded.description <> '' THEN excluded.description
                     ELSE tenders.description END,
    procedure_number=excluded.procedure_number,
    institution=CASE WHEN excluded.institution <> '' THEN excluded.institution
                     ELSE tenders.institution END,
    procedure_type=excluded.procedure_type,
    modality=excluded.modality,object_classification=excluded.object_classification,
    item=excluded.item,line_number=excluded.line_number,awardee=excluded.awardee,
    currency=excluded.currency,amount=excluded.amount,exchange_rate=excluded.exchange_rate,
    awarded_crc=excluded.awarded_crc,status=excluded.status,
    source_url=excluded.source_url,last_seen=excluded.last_seen,
    publication_date=COALESCE(NULLIF(excluded.publication_date,''),tenders.publication_date),
    is_it=CASE WHEN excluded.description <> '' THEN excluded.is_it ELSE tenders.is_it END,
    it_score=CASE WHEN excluded.description <> '' THEN excluded.it_score ELSE tenders.it_score END,
    it_priority=CASE WHEN excluded.description <> '' THEN excluded.it_priority ELSE tenders.it_priority END,
    it_labels=CASE WHEN excluded.description <> '' THEN excluded.it_labels ELSE tenders.it_labels END,
    classification_version=CASE WHEN excluded.description <> '' THEN excluded.classification_version
                                ELSE tenders.classification_version END''')


OBS_UPSERT = text('''INSERT INTO observatory_tenders (
 procedure_number,description,institution,procedure_type,modality,
 object_classification,publication_date,closing_date,status,currency,
 amount,source_url,archive_month,first_seen,last_seen,
 is_it,it_score,it_priority,it_labels,classification_version)
 VALUES (:procedure_number,:description,:institution,:procedure_type,:modality,
 :object_classification,:publication_date,:closing_date,:status,:currency,
 :amount,:source_url,:archive_month,:first_seen,:last_seen,
 :is_it,:it_score,:it_priority,:it_labels,:classification_version)
 ON CONFLICT(procedure_number) DO UPDATE SET
 description=CASE WHEN excluded.description <> '' THEN excluded.description
                  ELSE observatory_tenders.description END,
 institution=CASE WHEN excluded.institution <> '' THEN excluded.institution
                  ELSE observatory_tenders.institution END,
 procedure_type=CASE WHEN excluded.procedure_type <> '' THEN excluded.procedure_type
                  ELSE observatory_tenders.procedure_type END,
 modality=CASE WHEN excluded.modality <> '' THEN excluded.modality
               ELSE observatory_tenders.modality END,
 object_classification=CASE WHEN excluded.object_classification <> ''
                 THEN excluded.object_classification ELSE observatory_tenders.object_classification END,
 publication_date=COALESCE(excluded.publication_date,observatory_tenders.publication_date),
 closing_date=COALESCE(excluded.closing_date,observatory_tenders.closing_date),
 currency=CASE WHEN excluded.currency <> '' THEN excluded.currency
               ELSE observatory_tenders.currency END,
 amount=COALESCE(excluded.amount,observatory_tenders.amount),
 source_url=excluded.source_url,
 archive_month=CASE WHEN excluded.archive_month > observatory_tenders.archive_month
                    THEN excluded.archive_month ELSE observatory_tenders.archive_month END,
 last_seen=excluded.last_seen,
 is_it=excluded.is_it,it_score=excluded.it_score,
 it_priority=excluded.it_priority,it_labels=excluded.it_labels,
 classification_version=excluded.classification_version''')


def utcnow():
    return datetime.now(timezone.utc).isoformat()


_INITIALIZED_ENGINE = None


def init_db():
    global _INITIALIZED_ENGINE
    if _INITIALIZED_ENGINE is engine:
        backfill_classifications(limit=500)
        return
    statements = DDL
    if engine.dialect.name == 'sqlite':
        statements = [s.replace('BIGSERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT')
                      for s in DDL]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        existing = {col['name'] for col in inspect(conn).get_columns('tenders')}
        for name, kind in (('is_it', 'INTEGER'), ('it_score', 'INTEGER'),
                           ('it_priority', 'VARCHAR(20)'), ('it_labels', 'TEXT'),
                           ('publication_date', 'VARCHAR(10)'),
                           ('classification_version', 'INTEGER')):
            if name not in existing:
                if engine.dialect.name == 'postgresql':
                    conn.execute(text(f'ALTER TABLE tenders ADD COLUMN IF NOT EXISTS {name} {kind}'))
                else:
                    conn.execute(text(f'ALTER TABLE tenders ADD COLUMN {name} {kind}'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_tenders_it_priority ON tenders(is_it,it_score)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_tenders_publication ON tenders(publication_date)'))
        # Additive migration for user accounts created by V2-V5; never drop a user.
        user_columns = {col['name'] for col in inspect(conn).get_columns('users')}
        for name, kind in (('is_active', 'INTEGER NOT NULL DEFAULT 1'),
                           ('auth_version', 'INTEGER NOT NULL DEFAULT 1'),
                           ('created_at', 'TEXT'), ('last_login', 'TEXT')):
            if name not in user_columns:
                if engine.dialect.name == 'postgresql':
                    conn.execute(text(f'ALTER TABLE users ADD COLUMN IF NOT EXISTS {name} {kind}'))
                else:
                    conn.execute(text(f'ALTER TABLE users ADD COLUMN {name} {kind}'))
        # Additive migration for existing V8 databases; historical data remains intact.
        imported_cols = {col['name'] for col in inspect(conn).get_columns('observatory_imports')}
        for name in ('created', 'updated', 'unchanged'):
            if name not in imported_cols:
                conn.execute(text(f'ALTER TABLE observatory_imports ADD COLUMN {name} INTEGER DEFAULT 0'))
        conn.execute(text('''INSERT INTO sync_state(id,next_page,pages_done)
                             VALUES(1,1,0) ON CONFLICT(id) DO NOTHING'''))
    _INITIALIZED_ENGINE = engine
    # Only unclassified legacy records; a bounded batch per request avoids
    # heavy migrations on Render and preserves existing data.
    backfill_classifications(limit=500)


def backfill_classifications(limit=500):
    with engine.begin() as conn:
        rows = conn.execute(text('''SELECT id,description,object_classification,institution
            FROM tenders WHERE classification_version IS NULL OR classification_version < :version
            LIMIT :limit'''),
            {'limit': limit, 'version': CLASSIFICATION_VERSION}).mappings().all()
        payload = []
        for row in rows:
            info = classify_opportunity(row['description'], row['object_classification'])
            payload.append({'id': row['id'], 'is_it': info['is_it'],
                            'description': clean_description(row['description']),
                            'score': info['it_score'], 'priority': info['it_priority'],
                            'labels': json.dumps(info['it_labels'], ensure_ascii=False),
                            'institution': buyer_name(row['institution']),
                            'version': CLASSIFICATION_VERSION})
        if payload:
            conn.execute(text('''UPDATE tenders SET is_it=:is_it,it_score=:score,
                 it_priority=:priority,it_labels=:labels,institution=:institution,
                 description=:description, classification_version=:version WHERE id=:id'''), payload)
        # Newly introduced SOC language also applies to historical imports.
        historical = conn.execute(text('''SELECT procedure_number,description,object_classification
            FROM observatory_tenders
            WHERE classification_version IS NULL OR classification_version < :version
            LIMIT :limit'''), {'limit': limit, 'version': CLASSIFICATION_VERSION}).mappings().all()
        history_updates = []
        for old in historical:
            info = classify_opportunity(old['description'], old['object_classification'])
            history_updates.append({
                'number': old['procedure_number'], 'is_it': info['is_it'],
                'score': info['it_score'], 'priority': info['it_priority'],
                'labels': json.dumps(info['it_labels'], ensure_ascii=False),
                'version': CLASSIFICATION_VERSION,
            })
        if history_updates:
            conn.execute(text('''UPDATE observatory_tenders
                SET is_it=:is_it,it_score=:score,it_priority=:priority,
                    it_labels=:labels,classification_version=:version
                WHERE procedure_number=:number'''), history_updates)
    return len(payload) + len(history_updates)


def bootstrap_users():
    # One-time legacy import. Preserve existing V2-V5 rows and roles.
    # New databases are seeded from Render env credentials only once.
    with engine.begin() as conn:
        done = conn.execute(text(
            "SELECT value FROM app_settings WHERE key='legacy_user_bootstrap_v1'"
        )).scalar_one_or_none()
        if done:
            return
        existing = conn.execute(text('SELECT COUNT(*) FROM users')).scalar_one()
        if not existing:
            # Do not initialize a database with users but no administrator.
            admin_configured = any(os.getenv(env, '') for _, (role, env)
                                   in LEGACY_BOOTSTRAP_USERS.items() if role == 'admin')
            if not admin_configured:
                return
            now = utcnow()
            for email, (role, env_var) in LEGACY_BOOTSTRAP_USERS.items():
                password = os.getenv(env_var, '')
                if password:
                    conn.execute(text('''INSERT INTO users
                        (email,password_hash,role,updated_at,created_at)
                        VALUES (:email,:password,:role,:now,:now)
                        ON CONFLICT(email) DO NOTHING'''),
                        {'email': email, 'password': generate_password_hash(password),
                         'role': role, 'now': now})
            existing = conn.execute(text('SELECT COUNT(*) FROM users')).scalar_one()
        if existing:
            conn.execute(text('''INSERT INTO app_settings(key,value)
                VALUES ('legacy_user_bootstrap_v1','done')
                ON CONFLICT(key) DO NOTHING'''))


def current_user():
    # Read current DB role/status, rejecting revoked sessions immediately.
    email = session.get('email')
    session_version = session.get('auth_version')
    if not email or not isinstance(session_version, int):
        return None
    with engine.connect() as conn:
        account = conn.execute(text('''SELECT email,role,is_active,auth_version
            FROM users WHERE email=:email'''), {'email': email}).mappings().first()
    if not account or not account['is_active'] or account['auth_version'] != session_version:
        return None
    return {'email': account['email'], 'admin': account['role'] == 'admin',
            'role': account['role']}


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for('login'))
        return function(*args, **kwargs)
    return wrapper


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        actor = current_user()
        if not actor:
            return redirect(url_for('login'))
        if not actor['admin']:
            return 'Forbidden', 403
        return function(*args, **kwargs)
    return wrapper


# Conservative server-side validation for user administration.
EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]+$')


def normalized_email(value):
    email = str(value or '').strip().lower()
    return email if len(email) <= 255 and EMAIL_RE.fullmatch(email) else None


def valid_password(password):
    return isinstance(password, str) and 12 <= len(password) <= 128 and bool(password.strip())


def csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


@app.context_processor
def inject_csrf_token():
    return {'csrf_token': csrf_token()}


def require_form_csrf():
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and request.endpoint not in ('scheduled_refresh', 'scheduled_observatorio'):
        submitted = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token', '')
        if not submitted or not secrets.compare_digest(submitted, session.get('csrf_token', '')):
            return 'Solicitud rechazada: token de seguridad inválido. Recargá la página.', 400
    return None


def value(row, *aliases):
    values = {normalize(key): val for key, val in row.items()}
    for alias in aliases:
        val = values.get(normalize(alias))
        if val is not None and str(val).strip().lower() not in ('', 'nan', 'none'):
            return str(val).strip()
    return ''


def number(val):
    """Accept SICOP/CSV decimal points, decimal commas and grouped thousands."""
    val = str(val or '').strip().replace(' ', '')
    if not val:
        return None
    try:
        if ',' in val and '.' in val:
            # The rightmost separator is the decimal mark.
            if val.rfind(',') > val.rfind('.'):
                val = val.replace('.', '').replace(',', '.')
            else:
                val = val.replace(',', '')
        elif ',' in val:
            parts = val.split(',')
            if len(parts[-1]) == 3 and all(len(p) == 3 for p in parts[1:]):
                val = ''.join(parts)  # 1,500 or 1,234,567
            else:
                val = val.replace(',', '.')  # 1500,50
        return float(val)
    except (ValueError, OverflowError):
        return None


def upsert_rows(rows, conn=None):
    """Save one batch; callers may share a transaction with the sync cursor."""
    now = utcnow()
    processed = []
    for row in rows:
        raw_procedure = value(row, 'Número procedimiento', 'Número de procedimiento',
                              'procedure_number')
        if not raw_procedure:
            continue  # Blank/non-procedure CSV rows are ignored as before.
        procedure = canonical_procedure_number(raw_procedure)
        if not procedure:
            raise SICOPSourceError(
                'Número de procedimiento inválido o ambiguo en la fila '
                f'{len(processed) + 1} (longitud recibida: {len(raw_procedure)}). '
                'Revisá el HTML o los encabezados del CSV de SICOP. '
                'No se avanzó ni se truncó el dato.')
        item = value(row, 'Partida', 'item') or '0'
        line = value(row, 'Número de línea', 'Numero linea', 'line_number') or '0'
        record_id = f'{procedure}|{item}|{line}'
        # Preserve original IDs used by older installations.
        if len(record_id) > 220:
            raise SICOPSourceError('Partida o línea demasiado larga en el procedimiento '
                                   f'{procedure}; verificá la alineación de la tabla SICOP.')
        description = clean_description(value(row, 'Descripción',
                                        'Descripción del procedimiento',
                                        'Objeto contractual', 'Objeto de contratación',
                                        'description'))
        buyer = buyer_name(value(row, 'Institución', 'Institución contratante',
                                 'Entidad contratante', 'Entidad solicitante',
                                 'Nombre de la institución', 'Cliente',
                                 'Comprador', 'institution', 'client_name'))
        object_kind = value(row, 'Clasificación objeto', 'object_classification')
        scoring = classify_opportunity(description, object_kind)
        currency = value(row, 'Moneda', 'currency')
        status = value(row, 'Estado de procedimiento', 'status')
        if len(currency) > 20 or len(status) > 180:
            bad_field = 'Moneda' if len(currency) > 20 else 'Estado de procedimiento'
            raise SICOPSourceError(
                f'Campo {bad_field} demasiado largo en {procedure}. '
                'El formato del reporte SICOP pudo cambiar; se conservó el avance anterior.')
        processed.append({
            'id': record_id, 'procedure_number': procedure,
            'description': description, 'institution': buyer,
            'procedure_type': value(row, 'Tipo Procedimiento', 'procedure_type'),
            'modality': value(row, 'Tipo modalidad', 'modality'),
            'object_classification': object_kind,
            'item': item, 'line_number': line,
            'awardee': value(row, 'Adjudicatario', 'awardee'),
            'currency': currency,
            'amount': number(value(row, 'Monto', 'amount')),
            'exchange_rate': number(value(row, 'Tipo de cambio', 'exchange_rate')),
            'awarded_crc': number(value(row, 'Monto adjudicado en colones', 'awarded_crc')),
            'status': status,
            'source_url': SICOP_OPEN_DATA_URL, 'first_seen': now, 'last_seen': now,
            'publication_date': date_from_row(row),
            'classification_version': CLASSIFICATION_VERSION,
            'is_it': scoring['is_it'], 'it_score': scoring['it_score'],
            'it_priority': scoring['it_priority'],
            'it_labels': json.dumps(scoring['it_labels'], ensure_ascii=False),
        })
    if conn is not None:
        if processed:
            conn.execute(UPSERT, processed)
    else:
        with engine.begin() as transaction:
            if processed:
                transaction.execute(UPSERT, processed)
    return len(processed)



def _store_observatorio_batch(conn, batch):
    """Compare existing procedures and write only new or materially changed rows.

    A repeated import does *not* touch last_seen on unchanged rows. The
    comparison mirrors the merge rules of OBS_UPSERT (blank incoming values
    must never wipe known description, institution, dates or amount).
    """
    from sqlalchemy import bindparam
    numbers = [record['procedure_number'] for record in batch]
    query = text("SELECT * FROM observatory_tenders WHERE procedure_number IN :ids").bindparams(
        bindparam('ids', expanding=True))
    existing = {row['procedure_number']: dict(row) for row in
                conn.execute(query, {'ids': numbers}).mappings()}
    # These fields are materially relevant to catalog display or classification.
    fields = ('description','institution','procedure_type','modality',
              'object_classification','publication_date','closing_date',
              'currency','amount','is_it','it_score','it_priority','it_labels',
              'classification_version')
    new_count = changed_count = unchanged_count = 0
    changes = []
    for incoming in batch:
        old = existing.get(incoming['procedure_number'])
        if old is None:
            new_count += 1
            changes.append(incoming)
            continue
        def effective_value(field):
            new = incoming[field]
            if field in ('publication_date','closing_date','amount'):
                return new if new is not None else old[field]
            if field in ('description','institution','procedure_type','modality',
                         'object_classification','currency'):
                return new if new not in ('', None) else old[field]
            return new
        if any(effective_value(field) != old[field] for field in fields):
            changed_count += 1
            changes.append(incoming)
        else:
            unchanged_count += 1
    if changes:
        conn.execute(OBS_UPSERT, changes)
    return new_count, changed_count, unchanged_count


def import_observatorio(file_obj, month):
    # Import one official monthly ZIP without clearing existing rows.
    month = validate_month(month)
    now = utcnow()
    batch, processed = [], 0
    created = updated = unchanged = 0
    for row in iter_carteles(file_obj):
        description = clean_description(row['description'])
        score = classify_opportunity(description, row['object_classification'])
        batch.append({
            'procedure_number': row['procedure_number'], 'description': description,
            'institution': buyer_name(row['institution']),
            'procedure_type': row['procedure_type'], 'modality': row['modality'],
            'object_classification': row['object_classification'],
            'publication_date': row['publication_date'], 'closing_date': row['closing_date'],
            'status': 'Vigencia por verificar en SICOP',
            'currency': row['currency'], 'amount': number(row['amount']),
            'source_url': official_archive_url(month), 'archive_month': month,
            'first_seen': now, 'last_seen': now,
            'is_it': score['is_it'], 'it_score': score['it_score'],
            'it_priority': score['it_priority'],
            'it_labels': json.dumps(score['it_labels'], ensure_ascii=False),
            'classification_version': CLASSIFICATION_VERSION,
        })
        if len(batch) >= 500:
            with engine.begin() as conn:
                a, b, c = _store_observatorio_batch(conn, batch)
            created += a; updated += b; unchanged += c
            processed += len(batch)
            batch = []
    if batch:
        with engine.begin() as conn:
            a, b, c = _store_observatorio_batch(conn, batch)
        created += a; updated += b; unchanged += c
        processed += len(batch)
    with engine.begin() as conn:
        conn.execute(text('''INSERT INTO observatory_imports(month,imported_at,records,status,message)
             VALUES(:month,:at,:records,'complete','ZIP DetalleCarteles procesado')
             ON CONFLICT(month) DO UPDATE SET imported_at=excluded.imported_at,
             records=excluded.records,status=excluded.status,message=excluded.message'''),
             {'month': month, 'at': utcnow(), 'records': processed})
        conn.execute(text('''UPDATE observatory_imports SET created=:created,
            updated=:updated, unchanged=:unchanged WHERE month=:month'''),
            {'created': created, 'updated': updated,
             'unchanged': unchanged, 'month': month})
    return {'status': 'complete', 'month': month, 'processed': processed,
            'created': created, 'updated': updated, 'unchanged': unchanged,
            'message': 'Carteles del ZIP incorporados. Vigencia pendiente de verificar en SICOP.'}


def sync_observatorio(month):
    # Only download from a fixed, trusted URL.
    with download_archive(month) as archive:
        return import_observatorio(archive, month)


def sync_recent_observatorio():
    """Review at most one stale recent month per call. No historical reset.

    The first call updates the older month if needed; subsequent calls bring
    the current month up to date. A monthly ZIP replaces neither the users nor
    any other saved month. Idempotent imports compare new/changed rows.
    """
    for month in recent_months():
        with engine.begin() as conn:
            imported = conn.execute(text("SELECT imported_at FROM observatory_imports WHERE month=:month AND status='complete'"), {'month': month}).scalar()
        if due_for_refresh(imported, hours=max(1, min(168, int(os.getenv('OBS_REFRESH_HOURS', '18'))))):
            report = sync_observatorio(month)
            report['mode'] = 'incremental'
            report['status'] = 'partial' if month != recent_months()[-1] else 'complete'
            return report
    return {'status':'complete', 'mode':'incremental', 'processed':0,
            'created':0,'updated':0,'unchanged':0,
            'message':'No hay meses recientes pendientes de revisión.'}


def start_lease():
    """Prevent two Render workers/schedulers importing the same cursor concurrently."""
    lease_id = uuid.uuid4().hex
    now = utcnow()
    until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    with engine.begin() as conn:
        result = conn.execute(text('''UPDATE sync_state
            SET lock_id=:lease_id,locked_until=:until
            WHERE id=1 AND (lock_id IS NULL OR locked_until<:now)'''),
            {'lease_id': lease_id, 'until': until, 'now': now})
        if result.rowcount != 1:
            raise SICOPSourceError('Ya hay una sincronización en curso. Intenta más tarde.')
        state = conn.execute(text('SELECT * FROM sync_state WHERE id=1')).mappings().one()
    return lease_id, dict(state)


def release_lease(lease_id):
    with engine.begin() as conn:
        conn.execute(text('''UPDATE sync_state SET lock_id=NULL,locked_until=NULL
                             WHERE id=1 AND lock_id=:lease_id'''), {'lease_id': lease_id})


def refresh_public_page():
    """Read *several* pages, committing each page + cursor atomically.

    Repeated invocations continue from PostgreSQL. Partial runs are expected;
    they are not reported as a complete SICOP import.
    """
    started = utcnow()
    started_clock = time.monotonic()
    count = page_count = 0
    status = 'partial'
    message = ''
    lease_id = None
    report = {}
    try:
        lease_id, state = start_lease()
        cursor = state['next_page']
        last_signature = state['last_signature']
        source = make_session()
        try:
            # Bootstrap a fresh server session; SICOP's report may require a cookie.
            bootstrap = get_page(source, SICOP_OPEN_DATA_URL, 1)
            page = bootstrap if cursor == 1 else None
            if cursor > 1:
                # A pending cursor can contain a signature created by an older
                # version of the extractor. Re-fetch the preceding page using
                # the *current* fingerprint before comparing with cursor.
                previous = get_page(source, SICOP_OPEN_DATA_URL, cursor - 1)
                last_signature = previous.signature
            while page_count < PAGES_PER_RUN:
                if time.monotonic() - started_clock >= MAX_RUN_SECONDS:
                    break
                if page is None:
                    # Slow down requests and verify duplicates before declaring an error.
                    if PAGE_DELAY_SECONDS:
                        time.sleep(PAGE_DELAY_SECONDS)
                    page = get_page(source, SICOP_OPEN_DATA_URL, cursor,
                                    previous_signature=last_signature)
                if page.rows and page.signature == last_signature:
                    # Defensive check (also protects tests/custom page providers).
                    raise SICOPSourceError(
                        f'La página {cursor} repite la anterior. No se avanzó el cursor.')
                if cursor > 1 and not page.rows:
                    # Only a confirmed empty page can signal end when there is no total.
                    if page.total_pages is not None and page.total_pages >= cursor:
                        raise SICOPSourceError(f'SICOP devolvió vacía la página {cursor} esperada.')
                    finished = True
                else:
                    finished = (page.total_pages is not None and cursor >= page.total_pages)
                    if page.total_pages is None and not page.rows:
                        raise SICOPSourceError('SICOP no devolvió registros ni total verificable.')

                with engine.begin() as conn:
                    saved = upsert_rows(page.rows, conn)
                    if page.rows and saved == 0:
                        raise SICOPSourceError(f'Ningún registro válido en página {cursor}.')
                    count += saved
                    page_count += 1
                    next_page = 1 if finished else cursor + 1
                    conn.execute(text('''UPDATE sync_state SET next_page=:next,
                        total_pages=:pages,total_results=:results,
                        pages_done=:done,last_signature=:signature,
                        locked_until=:until,updated_at=:now
                        WHERE id=1 AND lock_id=:lease_id'''), {
                        'next': next_page,
                        'pages': page.total_pages if page.total_pages is not None else state['total_pages'],
                        'results': page.total_results if page.total_results is not None else state['total_results'],
                        'done': 0 if finished else state['pages_done'] + page_count,
                        'signature': None if finished else page.signature,
                        'until': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                        'now': utcnow(), 'lease_id': lease_id,
                    })
                cursor = next_page
                last_signature = None if finished else page.signature
                if finished:
                    status = 'complete'
                    break
                page = None
            message = ('Se completó el recorrido de la fuente.' if status == 'complete'
                       else 'Avance guardado. Ejecuta otra sincronización para continuar.')
        finally:
            source.close()
    except Exception as exc:
        status = 'error'
        message = str(exc)[:450]
        raise SICOPSourceError(message) from exc
    finally:
        if lease_id is not None:
            release_lease(lease_id)
        with engine.begin() as conn:
            conn.execute(text('''INSERT INTO sync_runs(started_at,finished_at,status,imported,message)
                VALUES(:started,:finished,:status,:imported,:message)'''),
                {'started': started, 'finished': utcnow(), 'status': status,
                 'imported': count, 'message': message})
    with engine.begin() as conn:
        state = conn.execute(text('SELECT * FROM sync_state WHERE id=1')).mappings().one()
        stored = conn.execute(text('SELECT COUNT(*) FROM tenders')).scalar_one()
    report = {'status': status, 'processed': count, 'pages_processed': page_count,
              'next_page': state['next_page'], 'total_pages': state['total_pages'],
              'source_results': state['total_results'], 'stored': stored,
              'message': message}
    return report


@app.before_request
def boot():
    init_db()
    bootstrap_users()
    return require_form_csrf()


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        with engine.begin() as conn:
            user = conn.execute(text('SELECT * FROM users WHERE email=:email'),
                                {'email': email}).mappings().first()
        if user and user['is_active'] and check_password_hash(user['password_hash'], password):
            with engine.begin() as conn:
                conn.execute(text('UPDATE users SET last_login=:now WHERE email=:email'),
                             {'now': utcnow(), 'email': email})
            session.clear()
            session['email'] = email
            session['auth_version'] = user['auth_version']
            csrf_token()
            return redirect(url_for('index'))
        flash('Credenciales incorrectas.', 'error')
    return render_template('login.html')


@app.get('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))



@app.get('/admin/users')
@admin_required
def manage_users():
    with engine.connect() as conn:
        users = conn.execute(text('''SELECT email,role,is_active,created_at,
            last_login,updated_at FROM users ORDER BY role ASC,email ASC''')).mappings().all()
    return render_template('users.html', users=users, u=current_user())


@app.post('/admin/users/create')
@admin_required
def create_user():
    email = normalized_email(request.form.get('email'))
    password = request.form.get('password', '')
    role = request.form.get('role', 'user')
    if not email or not valid_password(password) or role not in ('admin', 'user'):
        flash('Revisá el correo, el rol y la contraseña (mínimo 12 caracteres).', 'error')
        return redirect(url_for('manage_users'))
    try:
        now = utcnow()
        with engine.begin() as conn:
            conn.execute(text('''INSERT INTO users
               (email,password_hash,role,is_active,auth_version,created_at,updated_at)
               VALUES (:email,:pwd,:role,1,1,:now,:now)'''),
               {'email': email, 'pwd': generate_password_hash(password),
                'role': role, 'now': now})
        flash('Usuario creado. Compartí su contraseña por un canal seguro.', 'ok')
    except IntegrityError:
        flash('Ese correo ya está registrado. Podés reactivarlo o cambiar su contraseña.', 'error')
    return redirect(url_for('manage_users'))


@app.post('/admin/users/password')
@admin_required
def reset_user_password():
    email = normalized_email(request.form.get('email'))
    password = request.form.get('password', '')
    if not email or not valid_password(password):
        flash('Ingresá un correo válido y una nueva contraseña de al menos 12 caracteres.', 'error')
        return redirect(url_for('manage_users'))
    with engine.begin() as conn:
        result = conn.execute(text('''UPDATE users SET password_hash=:pwd,
            auth_version=auth_version+1,updated_at=:now WHERE email=:email'''),
            {'pwd': generate_password_hash(password), 'now': utcnow(), 'email': email})
    if result.rowcount:
        flash('Contraseña actualizada. Las sesiones anteriores de esa cuenta fueron revocadas.', 'ok')
        if email == session.get('email'):
            session.clear()
            return redirect(url_for('login'))
    else:
        flash('No se encontró el usuario.', 'error')
    return redirect(url_for('manage_users'))


@app.post('/admin/users/role')
@admin_required
def change_user_role():
    email = normalized_email(request.form.get('email'))
    role = request.form.get('role')
    if not email or role not in ('admin', 'user'):
        flash('Correo o rol inválido.', 'error')
        return redirect(url_for('manage_users'))
    if email == session.get('email'):
        flash('No podés cambiar tu propio rol desde esta pantalla.', 'error')
        return redirect(url_for('manage_users'))
    with engine.begin() as conn:
        # Serialize concurrent admin-role changes so they cannot remove all admins.
        if engine.dialect.name == 'postgresql':
            conn.execute(text('SELECT pg_advisory_xact_lock(67523011)'))
        account = conn.execute(text('SELECT role,is_active FROM users WHERE email=:email'),
                               {'email': email}).mappings().first()
        if not account:
            flash('Usuario no encontrado.', 'error')
        elif account['role'] != role:
            if account['role'] == 'admin' and account['is_active'] and role != 'admin':
                admins = conn.execute(text('''SELECT COUNT(*) FROM users
                    WHERE role='admin' AND is_active=1''')).scalar_one()
                if admins <= 1:
                    flash('Debe permanecer al menos un administrador activo.', 'error')
                    return redirect(url_for('manage_users'))
            conn.execute(text('''UPDATE users SET role=:role,auth_version=auth_version+1,
                updated_at=:now WHERE email=:email'''),
                {'email': email, 'role': role, 'now': utcnow()})
            flash('Rol actualizado. La sesión anterior de esa cuenta fue revocada.', 'ok')
        else:
            flash('El usuario ya tiene ese rol.', 'ok')
    return redirect(url_for('manage_users'))


@app.post('/admin/users/status')
@admin_required
def change_user_status():
    email = normalized_email(request.form.get('email'))
    active = request.form.get('active')
    if not email or active not in ('0', '1'):
        flash('Datos de usuario inválidos.', 'error')
        return redirect(url_for('manage_users'))
    if email == session.get('email') and active == '0':
        flash('No podés desactivar tu propia cuenta.', 'error')
        return redirect(url_for('manage_users'))
    with engine.begin() as conn:
        # Serialize concurrent admin-role changes so they cannot remove all admins.
        if engine.dialect.name == 'postgresql':
            conn.execute(text('SELECT pg_advisory_xact_lock(67523011)'))
        account = conn.execute(text('SELECT role,is_active FROM users WHERE email=:email'),
                               {'email': email}).mappings().first()
        if not account:
            flash('Usuario no encontrado.', 'error')
        elif account['is_active'] != int(active):
            if account['role'] == 'admin' and account['is_active'] and active == '0':
                admins = conn.execute(text('''SELECT COUNT(*) FROM users
                    WHERE role='admin' AND is_active=1''')).scalar_one()
                if admins <= 1:
                    flash('Debe permanecer al menos un administrador activo.', 'error')
                    return redirect(url_for('manage_users'))
            conn.execute(text('''UPDATE users SET is_active=:active,
                auth_version=auth_version+1,updated_at=:now WHERE email=:email'''),
                {'active': int(active), 'now': utcnow(), 'email': email})
            flash('Estado actualizado. Las sesiones anteriores de esa cuenta fueron revocadas.', 'ok')
        else:
            flash('No hubo cambios.', 'ok')
    return redirect(url_for('manage_users'))

def present_row(row):
    item = dict(row)
    item['institution'] = buyer_name(item.get('institution'))
    item['client_display'] = item['institution'] or 'Cliente no informado'
    item['summary'] = brief_description(item.get('description'))
    item['publication_display'] = display_publication_date(item.get('publication_date'))
    try:
        item['tech_areas'] = json.loads(item.get('it_labels') or '[]')
    except (ValueError, TypeError):
        item['tech_areas'] = matched_areas(item.get('description'),
                                          item.get('object_classification'))
    # Make SOC opportunities easy to find without requiring technical skills.
    # Suggestions are evidence for human review, not confirmed SOC requirements.
    soc_labels = (AREA_LABELS['soc'], AREA_LABELS['soc_related'])
    item['tech_areas'].sort(key=lambda label: (0 if label in soc_labels else 1, label))
    radar = analyze_soc(item.get('description'), item.get('object_classification'))
    item['soc_tier'] = radar['tier']
    item['soc_label'] = radar['label']
    item['soc_evidence'] = radar['evidence']
    item['soc_reason'] = radar['reason']
    item['soc_potential'] = radar['tier'] == 'functional'
    return item


# Column names here are intentionally whitelisted. Query text is always bound.
COLUMN_FIELDS = {
    'client_contains': 'institution',
    'description_contains': 'description',
    'procedure_contains': 'procedure_number',
    'state_contains': 'status',
    'type_contains': 'procedure_type',
    'awardee_contains': 'awardee',
}
COLUMN_FILTER_KEYS = tuple(COLUMN_FIELDS) + (
    'column_priority', 'column_area', 'published_from', 'published_to',
    'amount_min', 'amount_max', 'publication_missing',
)


def search_filter():
    q = request.args.get('q', '').strip()[:200]
    status = request.args.get('status', '').strip()[:200]
    institution = request.args.get('institution', '').strip()[:200]
    tech = selected_filter(request.args.get('tech', 'noventiq'))
    source_filter = request.args.get('source','all')
    if source_filter not in ('all','observatorio','sicop'):
        source_filter = 'all'
    priority = request.args.get('priority', '').title()
    if priority not in ('Alta', 'Media', 'Baja'):
        priority = ''
    column = {name: request.args.get(name, '').strip()[:200]
              for name in COLUMN_FILTER_KEYS}
    if column['column_priority'] not in ('Alta', 'Media', 'Baja'):
        column['column_priority'] = ''
    if column['column_area'] not in AREA_LABELS:
        column['column_area'] = ''
    conditions, params = [], {}
    if q:
        conditions.append('''(LOWER(description) LIKE :q OR
                              LOWER(procedure_number) LIKE :q OR
                              LOWER(institution) LIKE :q)''')
        params['q'] = f'%{q.lower()}%'
    if status:
        conditions.append('status=:status')
        params['status'] = status
    if institution:
        conditions.append('institution=:institution')
        params['institution'] = institution
    if source_filter == 'observatorio':
        conditions.append("source_kind LIKE 'Observatorio%'")
    elif source_filter == 'sicop':
        conditions.append("source_kind LIKE 'Reporte SICOP%'")
    tech_where, tech_params = classified_sql(tech)
    if tech_where:
        conditions.append(tech_where)
        params.update(tech_params)
    if priority:
        conditions.append('it_priority=:priority')
        params['priority'] = priority
    for key, field in COLUMN_FIELDS.items():
        if column[key]:
            conditions.append(f"LOWER(COALESCE({field},'')) LIKE :{key}")
            params[key] = '%' + column[key].lower() + '%'
    if column['column_priority']:
        conditions.append('it_priority=:column_priority')
        params['column_priority'] = column['column_priority']
    if column['column_area']:
        if column['column_area'] == 'soc_related':
            # Same SOC family as the main commercial profile: direct and indirect.
            soc_clause, soc_args = classified_sql('soc_related')
            conditions.append(soc_clause)
            params.update(soc_args)
        else:
            conditions.append("COALESCE(it_labels,'') LIKE :column_area")
            params['column_area'] = '%' + json.dumps(AREA_LABELS[column['column_area']],
                                                     ensure_ascii=False) + '%'
    for key, comparator in (('published_from','>='), ('published_to','<=')):
        publication = parse_publication_date(column[key])
        # Date picker only uses ISO dates; a malicious/freeform input is ignored.
        if publication and publication == column[key]:
            conditions.append(f'publication_date {comparator} :{key}')
            params[key] = publication
        else:
            column[key] = ''
    if column['publication_missing'] == '1':
        conditions.append("(publication_date IS NULL OR publication_date='')")
    else:
        column['publication_missing'] = ''
    for key, comparator in (('amount_min','>='), ('amount_max','<=')):
        if column[key]:
            try:
                amount_value = float(column[key])
                if not math.isfinite(amount_value) or amount_value < 0:
                    raise ValueError('Invalid amount')
                params[key] = amount_value
                conditions.append(f'amount {comparator} :{key}')
            except ValueError:
                column[key] = ''
    where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
    return q, status, institution, tech, priority, column, where, params, source_filter


SORT_OPTIONS = (('relevance', 'Mayor relevancia'),
                ('publication', 'Publicación más reciente'),
                ('amount', 'Monto más alto'), ('client', 'Cliente A-Z'))
SORT_SQL = {
    'relevance': 'COALESCE(it_score,0) DESC, last_seen DESC, id ASC',
    'publication': "CASE WHEN publication_date IS NULL OR publication_date='' THEN 1 ELSE 0 END, publication_date DESC, COALESCE(it_score,0) DESC, id ASC",
    'amount': 'CASE WHEN amount IS NULL THEN 1 ELSE 0 END, amount DESC, id ASC',
    'client': 'institution ASC, id ASC',
}


def table_query_args(q, status, institution, tech, priority, column, per_page, sort, source_filter):
    args = {'q': q, 'status': status, 'institution': institution, 'tech': tech, 'source': source_filter,
            'priority': priority, 'per_page': per_page, 'sort': sort}
    args.update({k: v for k,v in column.items() if v})
    return args


# Filter chips use the same canonical parameters as CSV/pagination links.
# Removing a chip never changes a different active filter.
FILTER_DESCRIPTIONS = {
    'source': 'Origen',
    'q': 'Búsqueda', 'status': 'Estado (anterior)',
    'institution': 'Cliente (anterior)', 'priority': 'Prioridad (anterior)',
    'client_contains': 'Cliente', 'description_contains': 'Descripción',
    'published_from': 'Publicado desde', 'published_to': 'Publicado hasta',
    'publication_missing': 'Sin fecha', 'column_priority': 'Prioridad',
    'column_area': 'Especialidad', 'procedure_contains': 'Procedimiento',
    'state_contains': 'Estado', 'type_contains': 'Tipo de licitación',
    'amount_min': 'Monto desde', 'amount_max': 'Monto hasta',
    'awardee_contains': 'Adjudicatario',
}


def describe_filters(query_args):
    """Return removable, escaped-by-Jinja filter labels and links for the UI."""
    chips = []
    for key, value in query_args.items():
        if not value:
            continue
        if key == 'tech':
            if value == 'noventiq':
                continue
            label, shown = 'Perfil', dict(TECH_FILTER_OPTIONS).get(value, value)
            cleared = {**query_args, 'tech': 'noventiq'}
        elif key == 'source' and value == 'all':
            continue
        elif key in FILTER_DESCRIPTIONS:
            label = FILTER_DESCRIPTIONS[key]
            shown = 'Sí' if key == 'publication_missing' else value
            cleared = {**query_args, key: ''}
        else:
            continue
        chips.append({'label': label, 'value': str(shown)[:100],
                      'url': url_for('index', **cleared)})
    return chips


def positive_int(value, default, max_value):
    try:
        return max(1, min(max_value, int(value)))
    except (TypeError, ValueError):
        return default


@app.get('/')
@login_required
def index():
    q, status, institution, tech, priority, column, where, params, source_filter = search_filter()
    per_page = positive_int(request.args.get('per_page'), 50, 100)
    requested_page = positive_int(request.args.get('page'), 1, 100000000)
    sort = request.args.get('sort', 'relevance')
    if sort not in SORT_SQL:
        sort = 'relevance'
    order_by = SORT_SQL[sort]
    with engine.begin() as conn:
        matched = conn.execute(text('SELECT COUNT(*) FROM ' + CATALOG_FROM + where), params).scalar_one()
        pages = max(1, math.ceil(matched / per_page))
        page = min(requested_page, pages)
        rows = conn.execute(text('SELECT * FROM ' + CATALOG_FROM + where +
            ' ORDER BY ' + order_by + ' LIMIT :limit OFFSET :offset'),
            {**params, 'limit': per_page, 'offset': (page - 1) * per_page}).mappings().all()
        rows = [present_row(r) for r in rows]
        stats = conn.execute(text('''SELECT COUNT(*) AS total,
            COUNT(DISTINCT NULLIF(institution,'')) AS institutions,
            SUM(CASE WHEN is_it=1 THEN 1 ELSE 0 END) AS ti_total,
            SUM(CASE WHEN it_priority='Alta' THEN 1 ELSE 0 END) AS altas
            FROM ''' + CATALOG_FROM)).mappings().one()
        sync_state = conn.execute(text('SELECT * FROM sync_state WHERE id=1')).mappings().one()
        last_observatorio = conn.execute(text('SELECT * FROM observatory_imports ORDER BY imported_at DESC LIMIT 1')).mappings().first()
        last_run = conn.execute(text('''SELECT * FROM sync_runs
            ORDER BY id DESC LIMIT 1''')).mappings().first()
    query_args = table_query_args(q, status, institution, tech, priority,
                                  column, per_page, sort, source_filter)
    from zoneinfo import ZoneInfo
    current_month = datetime.now(ZoneInfo('America/Costa_Rica')).strftime('%Y%m')
    return render_template('index.html', rows=rows, stats=stats,
                           u=current_user(), q=q, status=status, source_filter=source_filter,
                           inst=institution, tech=tech, priority=priority,
                           tech_filter_options=TECH_FILTER_OPTIONS, page=page, pages=pages,
                           matched=matched, per_page=per_page, sync_state=sync_state,
                           page_numbers=range(max(1, page-2), min(pages, page+2)+1),
                           last_run=last_run, last_observatorio=last_observatorio,
                           storage_backend=engine.dialect.name,
                           current_month=current_month, column=column, sort=sort,
                           sort_options=SORT_OPTIONS, area_options=AREA_LABELS.items(),
                           query_args=query_args, active_chips=describe_filters(query_args),
                           column_active=any(column.values()) or bool(status or institution or priority),
                           column_filter_count=sum(bool(value) for value in column.values()))


def csv_import(file_obj, encoding):
    file_obj.seek(0)
    stream = io.TextIOWrapper(file_obj, encoding=encoding, newline='')
    try:
        sample = stream.read(5000)
        stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(stream, dialect=dialect)
        batch, count = [], 0
        for row in reader:
            batch.append(row)
            if len(batch) >= 500:
                count += upsert_rows(batch)
                batch = []
        if batch:
            count += upsert_rows(batch)
        return count
    finally:
        stream.detach()


@app.post('/admin/import')
@admin_required
def upload_csv():
    upload = request.files.get('file')
    if not upload or not upload.filename.lower().endswith('.csv'):
        flash('Selecciona un archivo CSV exportado de SICOP.', 'error')
        return redirect(url_for('index'))
    try:
        try:
            count = csv_import(upload.stream, 'utf-8-sig')
        except UnicodeDecodeError:
            count = csv_import(upload.stream, 'latin-1')
        flash(f'Se procesaron {count:,} filas del CSV. Los duplicados se actualizaron.', 'ok')
    except Exception as exc:
        flash('No se pudo importar el CSV: ' + str(exc)[:180], 'error')
    return redirect(url_for('index'))


def next_month(month):
    year, m = int(month[:4]), int(month[4:])
    return f'{year + (m == 12):04d}{1 if m == 12 else m+1:02d}'


@app.post('/admin/observatorio/history/reset')
@admin_required
def reset_observatorio_history():
    try:
        start_month = validate_month(request.form.get('start_month','202301'))
    except ObservatoryImportError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('index'))
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO app_settings(key,value) " 
                          "VALUES('observatorio_next_month',:month) " 
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value"),
                     {'month': start_month})
    flash(f'Histórico reiniciado desde {start_month}, sin borrar registros.', 'ok')
    return redirect(url_for('index'))


@app.post('/admin/observatorio/history/run')
@admin_required
def import_observatorio_history():
    # One month per request; resume from persisted cursor.
    from zoneinfo import ZoneInfo
    current = datetime.now(ZoneInfo('America/Costa_Rica')).strftime('%Y%m')
    start_month = request.form.get('start_month', '202301')
    try:
        start_month = validate_month(start_month)
    except ObservatoryImportError as exc:
        return jsonify({'status':'error','detail':str(exc)}), 400
    with engine.begin() as conn:
        month = conn.execute(text("SELECT value FROM app_settings WHERE key='observatorio_next_month'")).scalar() or start_month
    if month > current:
        return jsonify({'status': 'complete', 'message': 'Se recorrieron los meses previstos.'})
    try:
        with engine.begin() as conn:
            imported = conn.execute(text("SELECT 1 FROM observatory_imports WHERE month=:month AND status='complete'"),
                                    {'month': month}).scalar()
        report = {'processed': 0, 'month': month} if imported else sync_observatorio(month)
        upcoming = next_month(month)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO app_settings(key,value) VALUES('observatorio_next_month',:month) ON CONFLICT(key) DO UPDATE SET value=excluded.value"), {'month': upcoming})
        return jsonify({'status': 'complete' if upcoming > current else 'partial',
                        'month': month, 'next_month': upcoming,
                        'processed': report['processed'], 'skipped': bool(imported)})
    except (ObservatoryImportError, ValueError, OSError) as exc:
        return jsonify({'status':'error', 'month':month,'detail':str(exc)[:200]}),503


@app.post('/admin/observatorio/incremental/run')
@admin_required
def incremental_observatorio():
    try:
        return jsonify(sync_recent_observatorio())
    except (ObservatoryImportError, ValueError, OSError) as exc:
        return jsonify({'status':'error','detail':str(exc)[:260]}),503


@app.post('/admin/observatorio/import')
@admin_required
def import_observatorio_upload():
    upload = request.files.get('file')
    month = request.form.get('month','')
    if not upload or not upload.filename.lower().endswith('.zip'):
        flash('Seleccioná un ZIP mensual oficial del Observatorio.','error')
        return redirect(url_for('index'))
    try:
        report = import_observatorio(upload.stream, month)
        flash(f"Observatorio {month}: {report['processed']:,} registros procesados.", 'ok')
    except (ObservatoryImportError, ValueError, OSError) as exc:
        flash(f'No se pudo importar el ZIP: {str(exc)[:180]}', 'error')
    return redirect(url_for('index'))


@app.post('/admin/observatorio/sync')
@admin_required
def import_observatorio_online():
    month = request.form.get('month','') or request.args.get('month','')
    try:
        report = sync_observatorio(month)
        if request.headers.get('Accept') == 'application/json':
            return jsonify(report)
        flash(f"Observatorio {month}: {report['processed']:,} registros procesados.", 'ok')
    except (ObservatoryImportError, ValueError, OSError) as exc:
        if request.headers.get('Accept') == 'application/json':
            return jsonify({'status':'error','detail':str(exc)[:250]}), 503
        flash('No se pudo importar el mes: ' + str(exc)[:200], 'error')
    return redirect(url_for('index'))


@app.post('/tasks/observatorio')
def scheduled_observatorio():
    token = request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    expected = os.getenv('REFRESH_TOKEN', '')
    if not expected or not secrets.compare_digest(token, expected):
        return {'error': 'unauthorized'}, 401
    from zoneinfo import ZoneInfo
    month = request.args.get('month')
    try:
        return jsonify(sync_observatorio(month) if month else sync_recent_observatorio())
    except (ObservatoryImportError, ValueError, OSError) as exc:
        return {'status':'error','detail':str(exc)[:250]}, 503


@app.post('/admin/sync')
@admin_required
def manual_sync():
    try:
        report = refresh_public_page()
        flash(f"{report['pages_processed']} páginas y {report['processed']} filas procesadas. "
              + report['message'], 'ok')
    except SICOPSourceError as exc:
        flash('Falló la lectura de SICOP: ' + str(exc)[:250]
              + ' Los datos previamente guardados se conservan.', 'error')
    return redirect(url_for('index'))


@app.post('/admin/sync/run')
@admin_required
def api_manual_sync():
    """Small resumable block for the dashboard's single-click full sync."""
    try:
        return jsonify(refresh_public_page())
    except SICOPSourceError as exc:
        return jsonify({'status': 'error', 'detail': str(exc)[:300]}), 503


@app.post('/admin/sync/reset')
@admin_required
def reset_sync():
    with engine.begin() as conn:
        result = conn.execute(text('''UPDATE sync_state SET next_page=1,pages_done=0,
            total_pages=NULL,total_results=NULL,last_signature=NULL,updated_at=:now
            WHERE id=1 AND lock_id IS NULL'''), {'now': utcnow()})
    flash('Se reinició el recorrido, sin eliminar los registros guardados.'
          if result.rowcount else 'No es posible reiniciar durante una sincronización.',
          'ok' if result.rowcount else 'error')
    return redirect(url_for('index'))


@app.post('/tasks/refresh')
def scheduled_refresh():
    token = request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    expected = os.getenv('REFRESH_TOKEN', '')
    if not expected or not secrets.compare_digest(token, expected):
        return {'error': 'unauthorized'}, 401
    try:
        return jsonify(refresh_public_page())
    except SICOPSourceError as exc:
        return {'status': 'error', 'detail': str(exc)[:300]}, 500


@app.get('/api/tenders')
@login_required
def api_tenders():
    q, status, institution, tech, priority, column, where, params, source_filter = search_filter()
    per_page = positive_int(request.args.get('per_page'), 100, 500)
    page = positive_int(request.args.get('page'), 1, 100000000)
    sort = request.args.get('sort', 'relevance')
    if sort not in SORT_SQL:
        sort = 'relevance'
    with engine.begin() as conn:
        total = conn.execute(text('SELECT COUNT(*) FROM ' + CATALOG_FROM + where), params).scalar_one()
        rows = conn.execute(text('SELECT * FROM ' + CATALOG_FROM + where +
            ' ORDER BY ' + SORT_SQL[sort] + ' LIMIT :limit OFFSET :offset'),
            {**params, 'limit': per_page, 'offset': (page - 1) * per_page}).mappings().all()
    return jsonify({'items': [present_row(r) for r in rows], 'tech': tech, 'page': page,
                    'per_page': per_page, 'total': total,
                    'total_pages': math.ceil(total / per_page)})


@app.get('/export.csv')
@login_required
def export_csv():
    """Stream all matching database rows without a LIMIT or memory spike."""
    q, status, institution, tech, priority, column, where, params, source_filter = search_filter()
    sort = request.args.get('sort', 'relevance')
    if sort not in SORT_SQL:
        sort = 'relevance'
    columns = ['id', 'procedure_number', 'description', 'institution',
               'procedure_type', 'modality', 'object_classification', 'item',
               'line_number', 'awardee', 'currency', 'amount', 'exchange_rate',
               'awarded_crc', 'status', 'source_url', 'first_seen', 'last_seen',
               'is_it', 'it_score', 'it_priority', 'it_labels',
               'publication_date', 'closing_date', 'source_kind', 'classification_version']

    @stream_with_context
    def produce():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        with engine.connect() as conn:
            result = conn.execution_options(stream_results=True).execute(text(
                'SELECT ' + ','.join(columns) + ' FROM ' + CATALOG_FROM + where
                + ' ORDER BY ' + SORT_SQL[sort]), params)
            for record in result:
                writer.writerow([value if value is not None else '' for value in record])
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)

    return Response(produce(), mimetype='text/csv; charset=utf-8', headers={
        'Content-Disposition': 'attachment; filename="sicop-licitaciones.csv"',
        'Cache-Control': 'no-store',
    })


@app.get('/health')
def health():
    try:
        with engine.begin() as conn:
            conn.execute(text('SELECT 1'))
            db_counts = conn.execute(text('''SELECT
                (SELECT COUNT(*) FROM tenders) AS sicop,
                (SELECT COUNT(*) FROM observatory_tenders) AS observatorio''')).mappings().first()
        return {'status': 'ok', 'database': engine.dialect.name,
                'persistent_storage': engine.dialect.name == 'postgresql',
                'sicop_rows': db_counts['sicop'],
                'observatory_rows': db_counts['observatorio']}
    except Exception as exc:
        return {'status': 'error', 'detail': str(exc)[:100]}, 500
