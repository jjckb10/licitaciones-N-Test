import csv, io, os, re
from datetime import datetime, timezone
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from sqlalchemy import create_engine, text
import pandas as pd
import requests
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'change-me-in-azure')
DB_URL = os.getenv('DATABASE_URL', 'sqlite:////home/site/data/sicop.db')
if DB_URL.startswith('postgres://'): DB_URL = DB_URL.replace('postgres://','postgresql+psycopg://',1)
engine = create_engine(DB_URL, pool_pre_ping=True)
SICOP_OPEN_DATA_URL = os.getenv('SICOP_OPEN_DATA_URL','https://www.sicop.go.cr/moduloBid/report/RE_DatosAbiertosConcursosView.jsp')
ADMIN_USERS={'juanjose.solanocamacho@noventiq.com','jjckb@gmail.com'}
REGULAR_USERS={'alonso.fallasvargas@noventiq.com'}

DDL='''
CREATE TABLE IF NOT EXISTS tenders (
 id VARCHAR(180) PRIMARY KEY, description TEXT, procedure_number VARCHAR(120), institution TEXT,
 procedure_type TEXT, modality TEXT, object_classification TEXT, item TEXT, line_number TEXT,
 awardee TEXT, currency VARCHAR(20), amount DOUBLE PRECISION, exchange_rate DOUBLE PRECISION,
 awarded_crc DOUBLE PRECISION, status VARCHAR(160), source_url TEXT, first_seen TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS users (email VARCHAR(255) PRIMARY KEY, password_hash TEXT NOT NULL, role VARCHAR(20) NOT NULL);
CREATE TABLE IF NOT EXISTS sync_runs (
 id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, started_at TEXT, finished_at TEXT,
 status VARCHAR(40), imported INTEGER, message TEXT
);
'''

def init_db():
    ddl = DDL.replace('id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY','id INTEGER PRIMARY KEY AUTOINCREMENT') if engine.dialect.name == 'sqlite' else DDL
    with engine.begin() as c:
        for stmt in ddl.split(';'):
            if stmt.strip(): c.execute(text(stmt))

def seed_users():
    defaults={
      'juanjose.solanocamacho@noventiq.com':('admin','ADMIN_PASSWORD_1'),
      'jjckb@gmail.com':('admin','ADMIN_PASSWORD_2'),
      'alonso.fallasvargas@noventiq.com':('user','USER_PASSWORD_1')
    }
    with engine.begin() as c:
        for email,(role,envname) in defaults.items():
            exists=c.execute(text('SELECT email FROM users WHERE email=:e'),{'e':email}).first()
            pwd=os.getenv(envname,'')
            if not exists and pwd:
                c.execute(text('INSERT INTO users(email,password_hash,role) VALUES(:e,:p,:r)'),{'e':email,'p':generate_password_hash(pwd),'r':role})

def current_user():
    email=session.get('email')
    return {'email':email,'admin':session.get('role')=='admin'} if email else None

def login_required(fn):
    @wraps(fn)
    def wrap(*a,**k):
        if not current_user(): return redirect(url_for('login'))
        return fn(*a,**k)
    return wrap

@app.route('/login',methods=['GET','POST'])
def login():
    init_db(); seed_users()
    if request.method=='POST':
        email=request.form.get('email','').strip().lower(); pwd=request.form.get('password','')
        with engine.begin() as c: row=c.execute(text('SELECT * FROM users WHERE email=:e'),{'e':email}).mappings().first()
        if row and check_password_hash(row['password_hash'],pwd):
            session.clear(); session['email']=email; session['role']=row['role']; return redirect(url_for('index'))
        flash('Credenciales incorrectas.','error')
    return render_template('login.html')

@app.get('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

def user(): return current_user() or {'email':'','admin':False}

def norm(s): return re.sub(r'[^a-z0-9]+','_',str(s).strip().lower()).strip('_')
def val(row, *names):
    m={norm(k):v for k,v in row.items()}
    for n in names:
        v=m.get(norm(n))
        if v is not None and not pd.isna(v): return str(v).strip()
    return ''
def num(v):
    try: return float(str(v).replace(' ','').replace(',',''))
    except: return None

def upsert_rows(rows):
    now=datetime.now(timezone.utc).isoformat(); count=0
    sql=text('''INSERT INTO tenders (id,description,procedure_number,institution,procedure_type,modality,object_classification,item,line_number,awardee,currency,amount,exchange_rate,awarded_crc,status,source_url,first_seen,last_seen)
    VALUES (:id,:description,:procedure_number,:institution,:procedure_type,:modality,:object_classification,:item,:line_number,:awardee,:currency,:amount,:exchange_rate,:awarded_crc,:status,:source_url,:first_seen,:last_seen)
    ON CONFLICT(id) DO UPDATE SET description=:description,institution=:institution,procedure_type=:procedure_type,modality=:modality,object_classification=:object_classification,item=:item,awardee=:awardee,currency=:currency,amount=:amount,exchange_rate=:exchange_rate,awarded_crc=:awarded_crc,status=:status,last_seen=:last_seen''')
    with engine.begin() as c:
        for r in rows:
            pn=val(r,'Número procedimiento','Numero procedimiento','procedure_number')
            line=val(r,'Número de línea','Numero de linea','line_number') or '0'
            desc=val(r,'Descripción','Descripcion','description')
            if not pn: continue
            data={'id':f'{pn}|{line}','description':desc,'procedure_number':pn,'institution':val(r,'Institución','Institucion','institution'),'procedure_type':val(r,'Tipo Procedimiento','procedure_type'),'modality':val(r,'Tipo modalidad','modality'),'object_classification':val(r,'Clasificación objeto','Clasificacion objeto','object_classification'),'item':val(r,'Partida','item'),'line_number':line,'awardee':val(r,'Adjudicatario','awardee'),'currency':val(r,'Moneda','currency'),'amount':num(val(r,'Monto','amount')),'exchange_rate':num(val(r,'Tipo de cambio','exchange_rate')),'awarded_crc':num(val(r,'Monto adjudicado en colones','awarded_crc')),'status':val(r,'Estado de procedimiento','status'),'source_url':SICOP_OPEN_DATA_URL,'first_seen':now,'last_seen':now}
            c.execute(sql,data); count+=1
    return count

@app.before_request
def setup():
    init_db(); seed_users()

@app.route('/')
@login_required
def index():
    q=request.args.get('q','').strip(); status=request.args.get('status','').strip(); inst=request.args.get('institution','').strip()
    where=[]; p={}
    if q: where.append('(LOWER(description) LIKE :q OR LOWER(procedure_number) LIKE :q)'); p['q']=f'%{q.lower()}%'
    if status: where.append('status=:status'); p['status']=status
    if inst: where.append('institution=:inst'); p['inst']=inst
    w=(' WHERE '+' AND '.join(where)) if where else ''
    with engine.begin() as c:
        rows=c.execute(text('SELECT * FROM tenders'+w+' ORDER BY last_seen DESC LIMIT 500'),p).mappings().all()
        stats=c.execute(text('SELECT COUNT(*) total, COUNT(DISTINCT institution) institutions FROM tenders')).mappings().one()
        statuses=[x[0] for x in c.execute(text("SELECT DISTINCT status FROM tenders WHERE status<>'' ORDER BY status"))]
        institutions=[x[0] for x in c.execute(text("SELECT DISTINCT institution FROM tenders WHERE institution<>'' ORDER BY institution LIMIT 500"))]
    return render_template('index.html', rows=rows,stats=stats,statuses=statuses,institutions=institutions,u=user(),q=q,status=status,inst=inst)

@app.post('/admin/import')
@login_required
def upload_csv():
    if not user()['admin']: return ('Forbidden',403)
    f=request.files.get('file')
    if not f: flash('Selecciona un CSV exportado por SICOP.','error'); return redirect(url_for('index'))
    raw=f.read()
    for enc in ('utf-8-sig','latin-1'):
        try: txt=raw.decode(enc); break
        except UnicodeDecodeError: pass
    dialect=csv.Sniffer().sniff(txt[:5000], delimiters=',;\t')
    rows=list(csv.DictReader(io.StringIO(txt), dialect=dialect))
    n=upsert_rows(rows); flash(f'Se importaron/actualizaron {n} líneas.','ok'); return redirect(url_for('index'))

@app.post('/admin/sync')
@login_required
def sync():
    if not user()['admin']: return ('Forbidden',403)
    try:
        r=requests.get(SICOP_OPEN_DATA_URL,timeout=90,headers={'User-Agent':'SICOP-Hub-internal/1.0'}); r.raise_for_status()
        tables=pd.read_html(io.StringIO(r.text)); table=max(tables,key=len)
        n=upsert_rows(table.to_dict(orient='records'))
        flash(f'Sincronización completada: {n} líneas procesadas.','ok')
    except Exception as e:
        flash('La sincronización directa no pudo leer el portal. Usa la importación CSV oficial. Detalle: '+str(e)[:180],'error')
    return redirect(url_for('index'))

@app.get('/api/tenders')
@login_required
def api_tenders():
    with engine.begin() as c: rows=c.execute(text('SELECT * FROM tenders ORDER BY last_seen DESC LIMIT 1000')).mappings().all()
    return jsonify([dict(x) for x in rows])

@app.get('/health')
def health(): return {'status':'ok'}
