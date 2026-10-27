import csv, io, os, re
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import requests

app=Flask(__name__)
app.secret_key=os.getenv('SECRET_KEY','development-only-change-me')
DB_URL=os.getenv('DATABASE_URL','sqlite:////tmp/sicop.db')
if DB_URL.startswith('postgres://'): DB_URL=DB_URL.replace('postgres://','postgresql+psycopg://',1)
elif DB_URL.startswith('postgresql://'): DB_URL=DB_URL.replace('postgresql://','postgresql+psycopg://',1)
engine=create_engine(DB_URL,pool_pre_ping=True,pool_recycle=300)
SICOP_OPEN_DATA_URL=os.getenv('SICOP_OPEN_DATA_URL','https://www.sicop.go.cr/moduloBid/report/RE_DatosAbiertosConcursosView.jsp')

USERS={
 'juanjose.solanocamacho@noventiq.com':('admin','ADMIN_PASSWORD_1'),
 'jjckb@gmail.com':('admin','ADMIN_PASSWORD_2'),
 'alonso.fallasvargas@noventiq.com':('user','USER_PASSWORD_1'),
 'diana.diaz@noventiq.com':('user','USER_PASSWORD_1'),
 'juan.marincabezas@noventiq.com':('user','USER_PASSWORD_1'),
 'a.wong@noventiq.com':('user','USER_PASSWORD_1'),
 'ziomara.chavarria@noventiq.com':('user','USER_PASSWORD_1'),
}

DDL=[
'''CREATE TABLE IF NOT EXISTS users(email VARCHAR(255) PRIMARY KEY,password_hash TEXT NOT NULL,role VARCHAR(20) NOT NULL,updated_at TEXT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS tenders(id VARCHAR(220) PRIMARY KEY,description TEXT,procedure_number VARCHAR(140),institution TEXT,procedure_type TEXT,modality TEXT,object_classification TEXT,item TEXT,line_number TEXT,awardee TEXT,currency VARCHAR(20),amount DOUBLE PRECISION,exchange_rate DOUBLE PRECISION,awarded_crc DOUBLE PRECISION,status VARCHAR(180),source_url TEXT,first_seen TEXT,last_seen TEXT)''',
'''CREATE TABLE IF NOT EXISTS sync_runs(id BIGSERIAL PRIMARY KEY,started_at TEXT,finished_at TEXT,status VARCHAR(40),imported INTEGER,message TEXT)''',
'''CREATE INDEX IF NOT EXISTS ix_tenders_procedure ON tenders(procedure_number)''',
'''CREATE INDEX IF NOT EXISTS ix_tenders_status ON tenders(status)'''
]

def init_db():
    stmts=DDL
    if engine.dialect.name=='sqlite': stmts=[x.replace('BIGSERIAL PRIMARY KEY','INTEGER PRIMARY KEY AUTOINCREMENT') for x in DDL]
    with engine.begin() as c:
        for stmt in stmts: c.execute(text(stmt))

def sync_users():
    now=datetime.now(timezone.utc).isoformat()
    with engine.begin() as c:
        for email,(role,env) in USERS.items():
            pwd=os.getenv(env,'')
            row=c.execute(text('SELECT email,role FROM users WHERE email=:e'),{'e':email}).mappings().first()
            if not row and pwd:
                c.execute(text('INSERT INTO users(email,password_hash,role,updated_at) VALUES(:e,:p,:r,:u)'),{'e':email,'p':generate_password_hash(pwd),'r':role,'u':now})
            elif row and row['role']!=role:
                c.execute(text('UPDATE users SET role=:r,updated_at=:u WHERE email=:e'),{'e':email,'r':role,'u':now})

def current_user():
    if not session.get('email'): return None
    return {'email':session['email'],'admin':session.get('role')=='admin'}

def login_required(fn):
    @wraps(fn)
    def wrapper(*args,**kwargs):
        if not current_user(): return redirect(url_for('login'))
        return fn(*args,**kwargs)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args,**kwargs):
        if not current_user(): return redirect(url_for('login'))
        if not current_user()['admin']: return ('Forbidden',403)
        return fn(*args,**kwargs)
    return wrapper

def norm(s): return re.sub(r'[^a-z0-9]+','_',str(s).strip().lower()).strip('_')
def val(row,*names):
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
    sql=text('''INSERT INTO tenders(id,description,procedure_number,institution,procedure_type,modality,object_classification,item,line_number,awardee,currency,amount,exchange_rate,awarded_crc,status,source_url,first_seen,last_seen) VALUES(:id,:description,:procedure_number,:institution,:procedure_type,:modality,:object_classification,:item,:line_number,:awardee,:currency,:amount,:exchange_rate,:awarded_crc,:status,:source_url,:first_seen,:last_seen) ON CONFLICT(id) DO UPDATE SET description=:description,institution=:institution,procedure_type=:procedure_type,modality=:modality,object_classification=:object_classification,item=:item,awardee=:awardee,currency=:currency,amount=:amount,exchange_rate=:exchange_rate,awarded_crc=:awarded_crc,status=:status,source_url=:source_url,last_seen=:last_seen''')
    with engine.begin() as c:
        for r in rows:
            pn=val(r,'Número procedimiento','Numero procedimiento','procedure_number')
            line=val(r,'Número de línea','Numero de linea','line_number') or '0'
            item=val(r,'Partida','item') or '0'
            if not pn: continue
            d={'id':f'{pn}|{item}|{line}','description':val(r,'Descripción','Descripcion','description'),'procedure_number':pn,'institution':val(r,'Institución','Institucion','institution'),'procedure_type':val(r,'Tipo Procedimiento','procedure_type'),'modality':val(r,'Tipo modalidad','modality'),'object_classification':val(r,'Clasificación objeto','Clasificacion objeto','object_classification'),'item':item,'line_number':line,'awardee':val(r,'Adjudicatario','awardee'),'currency':val(r,'Moneda','currency'),'amount':num(val(r,'Monto','amount')),'exchange_rate':num(val(r,'Tipo de cambio','exchange_rate')),'awarded_crc':num(val(r,'Monto adjudicado en colones','awarded_crc')),'status':val(r,'Estado de procedimiento','status'),'source_url':SICOP_OPEN_DATA_URL,'first_seen':now,'last_seen':now}
            c.execute(sql,d); count+=1
    return count

def refresh_public_page():
    started=datetime.now(timezone.utc).isoformat()
    try:
        r=requests.get(SICOP_OPEN_DATA_URL,timeout=120,headers={'User-Agent':'SICOP-Hub/2.0'}); r.raise_for_status()
        tables=pd.read_html(io.StringIO(r.text)); table=max(tables,key=len)
        count=upsert_rows(table.to_dict(orient='records'))
        status='ok'; msg='Página pública procesada'
    except Exception as e:
        count=0; status='error'; msg=str(e)[:500]
    with engine.begin() as c:
        c.execute(text('INSERT INTO sync_runs(started_at,finished_at,status,imported,message) VALUES(:s,:f,:st,:i,:m)'),{'s':started,'f':datetime.now(timezone.utc).isoformat(),'st':status,'i':count,'m':msg})
    if status=='error': raise RuntimeError(msg)
    return count

@app.before_request
def boot(): init_db(); sync_users()

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        email=request.form.get('email','').strip().lower(); pwd=request.form.get('password','')
        with engine.begin() as c: row=c.execute(text('SELECT * FROM users WHERE email=:e'),{'e':email}).mappings().first()
        if row and check_password_hash(row['password_hash'],pwd):
            session.clear(); session['email']=email; session['role']=row['role']; return redirect(url_for('index'))
        flash('Credenciales incorrectas.','error')
    return render_template('login.html')

@app.get('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.get('/')
@login_required
def index():
    q=request.args.get('q','').strip(); status=request.args.get('status','').strip(); inst=request.args.get('institution','').strip()
    where=[]; p={}
    if q: where.append('(LOWER(description) LIKE :q OR LOWER(procedure_number) LIKE :q)'); p['q']=f'%{q.lower()}%'
    if status: where.append('status=:status'); p['status']=status
    if inst: where.append('institution=:inst'); p['inst']=inst
    w=(' WHERE '+' AND '.join(where)) if where else ''
    with engine.begin() as c:
        rows=c.execute(text('SELECT * FROM tenders'+w+' ORDER BY last_seen DESC LIMIT 1000'),p).mappings().all()
        stats=c.execute(text('SELECT COUNT(*) total,COUNT(DISTINCT institution) institutions FROM tenders')).mappings().one()
        statuses=[x[0] for x in c.execute(text("SELECT DISTINCT status FROM tenders WHERE status<>'' ORDER BY status"))]
        institutions=[x[0] for x in c.execute(text("SELECT DISTINCT institution FROM tenders WHERE institution<>'' ORDER BY institution LIMIT 1000"))]
    return render_template('index.html',rows=rows,stats=stats,statuses=statuses,institutions=institutions,u=current_user(),q=q,status=status,inst=inst)

@app.post('/admin/import')
@admin_required
def upload_csv():
    f=request.files.get('file')
    if not f: flash('Selecciona un CSV oficial de SICOP.','error'); return redirect(url_for('index'))
    raw=f.read(); txt=None
    for enc in ('utf-8-sig','latin-1'):
        try: txt=raw.decode(enc); break
        except UnicodeDecodeError: pass
    dialect=csv.Sniffer().sniff(txt[:5000],delimiters=',;\t')
    n=upsert_rows(list(csv.DictReader(io.StringIO(txt),dialect=dialect)))
    flash(f'Se procesaron {n} líneas.','ok'); return redirect(url_for('index'))

@app.post('/admin/sync')
@admin_required
def manual_sync():
    try: flash(f'Sincronización completada: {refresh_public_page()} líneas procesadas.','ok')
    except Exception as e: flash('Falló la lectura directa. Importa el CSV oficial. '+str(e)[:180],'error')
    return redirect(url_for('index'))

@app.post('/tasks/refresh')
def scheduled_refresh():
    token=request.headers.get('Authorization','').removeprefix('Bearer ').strip()
    expected=os.getenv('REFRESH_TOKEN','')
    if not expected or token!=expected: return ({'error':'unauthorized'},401)
    try: return {'status':'ok','processed':refresh_public_page()}
    except Exception as e: return ({'status':'error','detail':str(e)[:300]},500)

@app.get('/api/tenders')
@login_required
def api_tenders():
    with engine.begin() as c: rows=c.execute(text('SELECT * FROM tenders ORDER BY last_seen DESC LIMIT 5000')).mappings().all()
    return jsonify([dict(x) for x in rows])

@app.get('/health')
def health():
    try:
        with engine.begin() as c: c.execute(text('SELECT 1'))
        return {'status':'ok','database':engine.dialect.name}
    except Exception as e: return ({'status':'error','detail':str(e)[:100]},500)
