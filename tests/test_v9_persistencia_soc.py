"""Offline tests for persistence, idempotent incremental imports and SOC tiers."""
import ast
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import zipfile

from sqlalchemy import create_engine, text

from app.catalog_sql import CATALOG_FROM
from app.incremental import due_for_refresh, recent_months
from app.soc_radar import analyze_soc
from app.technology import classify_opportunity, classified_sql, AREA_LABELS

ROOT = Path(__file__).resolve().parents[1]


def _zip(description, amount='2500'):
    stream = BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('DetalleCarteles.csv',
            'NRO_PROCEDIMIENTO;CEDULA_INSTITUCION;DESCRIPCION;MONTO_ESTIMADO;FECHA_PUBLICACION\n'
            '2026LE-000004-0008600001;3007123456;'+description+';'+amount+';2026-10-20\n')
        archive.writestr('InstitucionesRegistradas.csv',
            'CEDULA;NOMBRE_INSTITUCION\n3007123456;Consejo de Transporte Publico\n')
    stream.seek(0)
    return stream


def _importer_db():
    """Extract exact application functions without importing Flask in CI."""
    from app.observatorio import validate_month,iter_carteles,official_archive_url
    from app.presentation import clean_description,buyer_name
    source = ast.parse((ROOT/'app/main.py').read_text(encoding='utf-8'))
    decl = {}
    functions=[]
    for node in source.body:
        if isinstance(node,ast.Assign):
            names=[t.id for t in node.targets if isinstance(t,ast.Name)]
            if 'DDL' in names: decl['ddl']=ast.literal_eval(node.value)
            if 'OBS_UPSERT' in names: decl['upsert']=node.value.args[0].value
        if isinstance(node,ast.FunctionDef) and node.name in ('_store_observatorio_batch','import_observatorio'):
            functions.append(node)
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        for stmt in decl['ddl']:
            conn.execute(text(stmt.replace('BIGSERIAL PRIMARY KEY','INTEGER PRIMARY KEY AUTOINCREMENT')))
    ns={'engine':engine,'OBS_UPSERT':text(decl['upsert']),'validate_month':validate_month,
        'iter_carteles':iter_carteles,'official_archive_url':official_archive_url,
        'clean_description':clean_description,'buyer_name':buyer_name,
        'classify_opportunity':classify_opportunity,'CLASSIFICATION_VERSION':7,
        'json':json,'text':text,'number':lambda s:float(s) if s else None,
        'utcnow':lambda:datetime.now(timezone.utc).isoformat()}
    exec(compile(ast.Module(body=functions,type_ignores=[]), '<v9>', 'exec'), ns)
    return ns


def test_second_import_only_compares_and_does_not_touch_saved_row():
    ns=_importer_db()
    first=ns['import_observatorio'](_zip('Servicio SOC y monitoreo de seguridad'),'202610')
    assert first['created']==1 and first['updated']==0 and first['unchanged']==0
    with ns['engine'].begin() as conn:
        before=conn.execute(text('SELECT first_seen,last_seen FROM observatory_tenders')).one()
    second=ns['import_observatorio'](_zip('Servicio SOC y monitoreo de seguridad'),'202610')
    assert (second['created'],second['updated'],second['unchanged'])==(0,0,1)
    with ns['engine'].begin() as conn:
        after=conn.execute(text('SELECT first_seen,last_seen FROM observatory_tenders')).one()
        count=conn.execute(text('SELECT COUNT(*) FROM observatory_tenders')).scalar_one()
    assert before==after and count==1


def test_change_updates_only_one_record_without_deleting_others():
    ns=_importer_db()
    ns['import_observatorio'](_zip('Monitoreo tecnológico y respuesta a incidentes'),'202610')
    report=ns['import_observatorio'](_zip('Monitoreo tecnológico y respuesta a incidentes',amount='3000'),'202610')
    assert report['created']==0 and report['updated']==1 and report['unchanged']==0
    with ns['engine'].begin() as conn:
        assert conn.execute(text('SELECT amount FROM observatory_tenders')).scalar_one()==3000


def test_no_soc_suggested_for_only_physical_security():
    assert analyze_soc('Servicio de guardas de seguridad y CCTV')['tier']=='none'
    assert analyze_soc('Vigilancia privada y patrullaje')['tier']=='none'


def test_guide_categories_explain_direct_indirect_and_complementary():
    assert analyze_soc('Servicio SOC 24/7')['tier']=='direct'
    assert analyze_soc('Contratación de monitoreo de infraestructura tecnológica y respuesta a incidentes 24/7')['tier']=='functional'
    assert analyze_soc('Renovación de firewall y análisis de vulnerabilidades')['tier']=='complementary'
    assert analyze_soc('Construcción de una cancha')['tier']=='none'


def test_soc_map_search_includes_complementary_procurement():
    label=AREA_LABELS['soc_complementary']
    result=classify_opportunity('Renovación de firewall Fortinet y análisis de vulnerabilidades')
    assert label in result['it_labels']
    sql,params=classified_sql('soc_map')
    db=create_engine('sqlite:///:memory:')
    with db.begin() as conn:
        conn.execute(text('CREATE TABLE tenders (it_labels TEXT)'))
        conn.execute(text('INSERT INTO tenders (it_labels) VALUES (:labels)'),
                     {'labels':json.dumps(result['it_labels'],ensure_ascii=False)})
        assert conn.execute(text('SELECT COUNT(*) FROM tenders WHERE '+sql),params).scalar_one()==1


def test_recent_months_and_cache_grace_period():
    from zoneinfo import ZoneInfo
    now=datetime(2027,1,1,8,0,tzinfo=ZoneInfo('America/Costa_Rica'))
    assert recent_months(now)==('202612','202701')
    assert not due_for_refresh(now.isoformat(),now=now,hours=18)
    assert due_for_refresh(datetime(2026,12,30,tzinfo=now.tzinfo).isoformat(),now=now,hours=18)
    assert due_for_refresh(None,now=now)


def test_render_guard_does_not_allow_tmp_sqlite_when_required():
    code=(ROOT/'app/main.py').read_text()
    assert 'if PERSISTENCE_REQUIRED and not os.getenv' in code
    assert 'Base de datos no persistente en Render' in code
    cfg=(ROOT/'render.yaml').read_text()
    assert 'REQUIRE_PERSISTENT_DB' in cfg


def test_incremental_routes_are_guarded_and_do_not_drop_tables():
    source=(ROOT/'app/main.py').read_text()
    assert "@app.post('/admin/observatorio/incremental/run')" in source
    assert '@admin_required\ndef incremental_observatorio' in source
    assert 'DROP TABLE' not in source.upper()
    html=(ROOT/'app/templates/index.html').read_text()
    assert 'Actualizar novedades sin reiniciar' in html
    assert 'r.soc_reason' in html
