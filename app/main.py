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
from werkzeug.security import check_password_hash, generate_password_hash

from app.sicop_source import SICOPSourceError, get_page, make_session, normalize
from app.technology import (TECH_FILTER_OPTIONS, matched_areas,
                            selected_filter, classified_sql, classify_opportunity)
from app.presentation import buyer_name, clean_description, brief_description
from app.publication import date_from_row, display_publication_date, parse_publication_date
from app.technology import AREA_LABELS, CLASSIFICATION_VERSION

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'development-only-change-me')
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CSV_UPLOAD_MB', '70')) * 1024 * 1024

DB_URL = os.getenv('DATABASE_URL', 'sqlite:////tmp/sicop.db')
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

USERS = {
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
       password_hash TEXT NOT NULL,role VARCHAR(20) NOT NULL,updated_at TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS tenders(id VARCHAR(220) PRIMARY KEY,
       description TEXT,procedure_number VARCHAR(140),institution TEXT,
       procedure_type TEXT,modality TEXT,object_classification TEXT,item TEXT,
       line_number TEXT,awardee TEXT,currency VARCHAR(20),amount DOUBLE PRECISION,
       exchange_rate DOUBLE PRECISION,awarded_crc DOUBLE PRECISION,status VARCHAR(180),
       source_url TEXT,first_seen TEXT,last_seen TEXT, publication_date VARCHAR(10),
       is_it INTEGER,it_score INTEGER,it_priority VARCHAR(20),it_labels TEXT,
       classification_version INTEGER)''',
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
    return len(payload)


def sync_users():
    now = utcnow()
    with engine.begin() as conn:
        for email, (role, env_var) in USERS.items():
            password = os.getenv(env_var, '')
            user = conn.execute(text('SELECT role FROM users WHERE email=:email'),
                                {'email': email}).mappings().first()
            if not user and password:
                conn.execute(text('''INSERT INTO users(email,password_hash,role,updated_at)
                                     VALUES(:email,:password,:role,:now)'''),
                             {'email': email, 'password': generate_password_hash(password),
                              'role': role, 'now': now})
            elif user and user['role'] != role:
                conn.execute(text('UPDATE users SET role=:role,updated_at=:now WHERE email=:email'),
                             {'role': role, 'now': now, 'email': email})


def current_user():
    return {'email': session['email'], 'admin': session.get('role') == 'admin'} \
        if session.get('email') else None


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
        if not current_user():
            return redirect(url_for('login'))
        if not current_user()['admin']:
            return 'Forbidden', 403
        return function(*args, **kwargs)
    return wrapper


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
        procedure = value(row, 'Número procedimiento', 'Número de procedimiento',
                          'procedure_number')
        item = value(row, 'Partida', 'item') or '0'
        line = value(row, 'Número de línea', 'Numero linea', 'line_number') or '0'
        if not procedure:
            continue
        record_id = f'{procedure}|{item}|{line}'
        # Retain the original id scheme so existing Render/PostgreSQL records are updated.
        if len(record_id) > 220:
            continue
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
        processed.append({
            'id': record_id, 'procedure_number': procedure,
            'description': description, 'institution': buyer,
            'procedure_type': value(row, 'Tipo Procedimiento', 'procedure_type'),
            'modality': value(row, 'Tipo modalidad', 'modality'),
            'object_classification': object_kind,
            'item': item, 'line_number': line,
            'awardee': value(row, 'Adjudicatario', 'awardee'),
            'currency': value(row, 'Moneda', 'currency'),
            'amount': number(value(row, 'Monto', 'amount')),
            'exchange_rate': number(value(row, 'Tipo de cambio', 'exchange_rate')),
            'awarded_crc': number(value(row, 'Monto adjudicado en colones', 'awarded_crc')),
            'status': value(row, 'Estado de procedimiento', 'status'),
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
    sync_users()


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        with engine.begin() as conn:
            user = conn.execute(text('SELECT * FROM users WHERE email=:email'),
                                {'email': email}).mappings().first()
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['email'] = email
            session['role'] = user['role']
            return redirect(url_for('index'))
        flash('Credenciales incorrectas.', 'error')
    return render_template('login.html')


@app.get('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


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
    return q, status, institution, tech, priority, column, where, params


SORT_OPTIONS = (('relevance', 'Mayor relevancia'),
                ('publication', 'Publicación más reciente'),
                ('amount', 'Monto más alto'), ('client', 'Cliente A-Z'))
SORT_SQL = {
    'relevance': 'COALESCE(it_score,0) DESC, last_seen DESC, id ASC',
    'publication': "CASE WHEN publication_date IS NULL OR publication_date='' THEN 1 ELSE 0 END, publication_date DESC, COALESCE(it_score,0) DESC, id ASC",
    'amount': 'CASE WHEN amount IS NULL THEN 1 ELSE 0 END, amount DESC, id ASC',
    'client': 'institution ASC, id ASC',
}


def table_query_args(q, status, institution, tech, priority, column, per_page, sort):
    args = {'q': q, 'status': status, 'institution': institution, 'tech': tech,
            'priority': priority, 'per_page': per_page, 'sort': sort}
    args.update({k: v for k,v in column.items() if v})
    return args


def positive_int(value, default, max_value):
    try:
        return max(1, min(max_value, int(value)))
    except (TypeError, ValueError):
        return default


@app.get('/')
@login_required
def index():
    q, status, institution, tech, priority, column, where, params = search_filter()
    per_page = positive_int(request.args.get('per_page'), 50, 100)
    requested_page = positive_int(request.args.get('page'), 1, 100000000)
    sort = request.args.get('sort', 'relevance')
    if sort not in SORT_SQL:
        sort = 'relevance'
    order_by = SORT_SQL[sort]
    with engine.begin() as conn:
        matched = conn.execute(text('SELECT COUNT(*) FROM tenders' + where), params).scalar_one()
        pages = max(1, math.ceil(matched / per_page))
        page = min(requested_page, pages)
        rows = conn.execute(text('SELECT * FROM tenders' + where +
            ' ORDER BY ' + order_by + ' LIMIT :limit OFFSET :offset'),
            {**params, 'limit': per_page, 'offset': (page - 1) * per_page}).mappings().all()
        rows = [present_row(r) for r in rows]
        stats = conn.execute(text('''SELECT COUNT(*) AS total,
            COUNT(DISTINCT NULLIF(institution,'')) AS institutions,
            SUM(CASE WHEN is_it=1 THEN 1 ELSE 0 END) AS ti_total,
            SUM(CASE WHEN it_priority='Alta' THEN 1 ELSE 0 END) AS altas
            FROM tenders''')).mappings().one()
        statuses = [x[0] for x in conn.execute(text(
            "SELECT DISTINCT status FROM tenders WHERE status<>'' ORDER BY status"))]
        institutions = [x[0] for x in conn.execute(text(
            "SELECT DISTINCT institution FROM tenders WHERE institution<>'' ORDER BY institution"))]
        sync_state = conn.execute(text('SELECT * FROM sync_state WHERE id=1')).mappings().one()
        last_run = conn.execute(text('''SELECT * FROM sync_runs
            ORDER BY id DESC LIMIT 1''')).mappings().first()
    return render_template('index.html', rows=rows, stats=stats, statuses=statuses,
                           institutions=institutions, u=current_user(), q=q,
                           status=status, inst=institution, tech=tech, priority=priority,
                           tech_filter_options=TECH_FILTER_OPTIONS, page=page, pages=pages,
                           matched=matched, per_page=per_page, sync_state=sync_state,
                           page_numbers=range(max(1, page-2), min(pages, page+2)+1),
                           last_run=last_run, column=column, sort=sort,
                           sort_options=SORT_OPTIONS, area_options=AREA_LABELS.items(),
                           query_args=table_query_args(q, status, institution, tech, priority,
                             column, per_page, sort))


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
    q, status, institution, tech, priority, column, where, params = search_filter()
    per_page = positive_int(request.args.get('per_page'), 100, 500)
    page = positive_int(request.args.get('page'), 1, 100000000)
    sort = request.args.get('sort', 'relevance')
    if sort not in SORT_SQL:
        sort = 'relevance'
    with engine.begin() as conn:
        total = conn.execute(text('SELECT COUNT(*) FROM tenders' + where), params).scalar_one()
        rows = conn.execute(text('SELECT * FROM tenders' + where +
            ' ORDER BY ' + SORT_SQL[sort] + ' LIMIT :limit OFFSET :offset'),
            {**params, 'limit': per_page, 'offset': (page - 1) * per_page}).mappings().all()
    return jsonify({'items': [present_row(r) for r in rows], 'tech': tech, 'page': page,
                    'per_page': per_page, 'total': total,
                    'total_pages': math.ceil(total / per_page)})


@app.get('/export.csv')
@login_required
def export_csv():
    """Stream all matching database rows without a LIMIT or memory spike."""
    q, status, institution, tech, priority, column, where, params = search_filter()
    sort = request.args.get('sort', 'relevance')
    if sort not in SORT_SQL:
        sort = 'relevance'
    columns = ['id', 'procedure_number', 'description', 'institution',
               'procedure_type', 'modality', 'object_classification', 'item',
               'line_number', 'awardee', 'currency', 'amount', 'exchange_rate',
               'awarded_crc', 'status', 'source_url', 'first_seen', 'last_seen',
               'is_it', 'it_score', 'it_priority', 'it_labels',
               'publication_date', 'classification_version']

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
                'SELECT ' + ','.join(columns) + ' FROM tenders' + where
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
        return {'status': 'ok', 'database': engine.dialect.name}
    except Exception as exc:
        return {'status': 'error', 'detail': str(exc)[:100]}, 500
