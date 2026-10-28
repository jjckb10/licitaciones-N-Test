"""Offline validations: official ZIP layouts, SOC semantic hints and catalog SQL.

The archive fixtures use the public SICOP open-data dictionary column names.
Does not claim live SICOP/Observatorio access or current offer availability.
"""
import ast
from pathlib import Path
from io import BytesIO
import json
import zipfile

from sqlalchemy import create_engine, text

from app.catalog_sql import CATALOG_FROM
from app.observatorio import iter_carteles, official_archive_url, ObservatoryImportError
from app.technology import (AREA_LABELS, classify_opportunity, classified_sql,
                            matched_areas, selected_filter, CLASSIFICATION_VERSION)


ROOT = Path(__file__).resolve().parents[1]


def fake_zip(rows, with_buyers=True):
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr('DetalleCarteles.csv', 'NRO_SICOP;CEDULA_INSTITUCION;NRO_PROCEDIMIENTO;DESCRIPCION;FECHA_PUBLICACION;FECHA_CIERRE_RECEPCION;TIPO_PROCEDIMIENTO\n' + rows)
        if with_buyers:
            zip_file.writestr('InstitucionesRegistradas.csv',
                              'CEDULA;NOMBRE_INSTITUCION\n3007123456;Consejo de Transporte Público\n')
    stream.seek(0)
    return stream


def example_cartel():
    return ('202610001;3007123456;2026LE-000004-0008600001;'
            'Servicio de Centro de Operaciones de Seguridad (SOC) Institucional;'
            '2026-10-15 08:00:00;2026-10-31 10:00:00;LICITACIÓN MENOR\n')


def test_official_zip_cartel_parses_buyer_and_real_deadline():
    row = list(iter_carteles(fake_zip(example_cartel())))[0]
    assert row['procedure_number'] == '2026LE-000004-0008600001'
    assert row['institution'] == 'Consejo de Transporte Público'
    assert row['publication_date'] == '2026-10-15'
    assert row['closing_date'] == '2026-10-31'
    assert row['status'] == 'Vigencia por verificar en SICOP'
    assert official_archive_url('202610').endswith('/202610.zip')


def test_soc_profile_includes_direct_explicit_and_implicit_signals():
    samples = [
        'Servicio de Centro de Operaciones de Seguridad (SOC) Institucional',
        'Contratación de servicios para el monitoreo, gestión y mitigación del riesgo en la infraestructura tecnológica institucional',
        'Herramienta de administración y respuesta de eventos de seguridad informática',
        'Servicio de monitoreo continuo de infraestructura tecnológica, correlación de eventos y respuesta a incidentes 24/7',
        'Licenciamiento y mantenimiento de Centros de Operaciones de Ciberseguridad',
        'Servicio para monitoreo de NOC y SOC',
    ]
    soc_predicate, args = classified_sql('soc_related')
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE tenders(id TEXT,description TEXT,it_labels TEXT,is_it INTEGER)'))
        for i, desc in enumerate(samples):
            entry = classify_opportunity(desc)
            assert entry['is_it'] == 1, desc
            conn.execute(text('INSERT INTO tenders VALUES(:id,:description,:labels,:is_it)'),
                         dict(id=str(i), description=desc,labels=json.dumps(entry['it_labels'],ensure_ascii=False),is_it=entry['is_it']))
        assert conn.execute(text('SELECT COUNT(*) FROM tenders WHERE '+soc_predicate),args).scalar_one() == len(samples)
    assert selected_filter('soc_related') == 'soc_related'
    assert AREA_LABELS['soc_related'].lower().startswith('soc potencial')


def test_generic_physical_security_is_not_soc():
    rows = [
        'Servicio de monitoreo de alarmas CCTV y vigilancia privada de las instalaciones',
        'Servicio de monitoreo y respuesta armada en sistemas de alarma de edificio',
        'Contratación de custodios de seguridad física y guardas para edificios',
        'SERVICIOS DE LIMPIEZA Y MANTENIMIENTO DE PARQUES',
        'Sociedad de monitoreo y vigilancia de parques nacionales',
    ]
    for row in rows:
        score = classify_opportunity(row)
        assert AREA_LABELS['soc_related'] not in score['it_labels'], row
        assert AREA_LABELS['soc'] not in score['it_labels'], row


def test_soc_related_categories_visible_as_subfilters():
    assert 'soc_related' in [k for k, _ in __import__('app.technology',fromlist=['TECH_FILTER_OPTIONS']).TECH_FILTER_OPTIONS]
    from pathlib import Path
    source = (ROOT/'app/templates/index.html').read_text()
    assert 'SOC y servicios equivalentes' in source
    assert 'value="soc_related"' in source
    assert 'column_area' in source
    from app.technology import SECURITY_KEYS
    assert 'soc_related' in SECURITY_KEYS


def test_catalog_prefers_observatorio_procedure_without_duplicate():
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as c:
        c.execute(text('''CREATE TABLE tenders(id TEXT,procedure_number TEXT,
            description TEXT,institution TEXT,procedure_type TEXT,modality TEXT,
            object_classification TEXT,item TEXT,line_number TEXT,awardee TEXT,
            currency TEXT,amount REAL,exchange_rate REAL,awarded_crc REAL,
            status TEXT,source_url TEXT,first_seen TEXT,last_seen TEXT,
            publication_date TEXT,is_it INTEGER,it_score INTEGER,it_priority TEXT,
            it_labels TEXT,classification_version INTEGER)'''))
        c.execute(text('''CREATE TABLE observatory_tenders(procedure_number TEXT,
            description TEXT,institution TEXT,procedure_type TEXT,modality TEXT,
            object_classification TEXT,publication_date TEXT,closing_date TEXT,
            status TEXT,currency TEXT,amount REAL,source_url TEXT,
            archive_month TEXT,first_seen TEXT,last_seen TEXT,is_it INTEGER,
            it_score INTEGER,it_priority TEXT,it_labels TEXT,classification_version INTEGER)'''))
        c.execute(text('''INSERT INTO tenders(id,procedure_number,description, institution,last_seen,item,line_number)
            VALUES ('one','2026LE-000004-0008600001','Servicio SOC','', '2026-10-25','1','1'),
                   ('two','2026LE-000004-0008600001','Servicio SOC','', '2026-10-26','1','2'),
                   ('third','2024LD-000028-0018300001','Monitoreo ciberseguridad','JASEC','2026-10-26','1','1')'''))
        c.execute(text('''INSERT INTO observatory_tenders (procedure_number,description,institution,
            publication_date,closing_date,status,it_score,is_it)
            VALUES ('2026LE-000004-0008600001','SOC institucional','Consejo de Transporte Público',
                    '2026-10-15','2026-10-31','Vigencia por verificar en SICOP',85,1)'''))
        rows=c.execute(text('SELECT procedure_number,institution,source_kind FROM '+CATALOG_FROM+' ORDER BY procedure_number')).all()
        assert rows==[
            ('2024LD-000028-0018300001','JASEC','Reporte SICOP / CSV'),
            ('2026LE-000004-0008600001','Consejo de Transporte Público','Observatorio (vigencia sin verificar)')]


def test_no_arbitrary_zip_member_extraction():
    data=BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('../other.csv','secret')
    data.seek(0)
    import pytest
    with pytest.raises(ObservatoryImportError,match='DetalleCarteles'):
        list(iter_carteles(data))


def test_importer_to_catalog_end_to_end_without_flask():
    """Execute real importer function extracted from main.py, with a test DB."""
    from app.observatorio import validate_month, iter_carteles
    from app.presentation import clean_description, buyer_name
    from app.technology import classify_opportunity
    from datetime import datetime, timezone
    from app.publication import parse_publication_date

    source = ast.parse((ROOT/'app/main.py').read_text(encoding='utf-8'))
    decl = {}
    fn = None
    for node in source.body:
        if isinstance(node, ast.Assign):
            keys=[x.id for x in node.targets if isinstance(x, ast.Name)]
            if 'DDL' in keys:decl['ddl']=ast.literal_eval(node.value)
            if 'OBS_UPSERT' in keys:decl['upsert']=node.value.args[0].value
        if isinstance(node, ast.FunctionDef) and node.name=='import_observatorio':fn=node
        if isinstance(node, ast.FunctionDef) and node.name=='_store_observatorio_batch':helper=node
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as c:
        for stmt in decl['ddl']:
            c.execute(text(stmt.replace('BIGSERIAL PRIMARY KEY','INTEGER PRIMARY KEY AUTOINCREMENT')))
    namespace = {
        'validate_month':validate_month,'iter_carteles':iter_carteles,
        'clean_description':clean_description,'buyer_name':buyer_name,
        'classify_opportunity':classify_opportunity,'official_archive_url':official_archive_url,
        'utcnow':lambda:'2026-10-28T00:00:00Z', 'number':lambda v:float(v) if v else None,
        'engine':engine,'OBS_UPSERT':text(decl['upsert']),'CLASSIFICATION_VERSION':CLASSIFICATION_VERSION,
        'json':json,'text':text,
    }
    exec(compile(ast.Module(body=[helper,fn], type_ignores=[]),'<import-function>','exec'),namespace)
    report=namespace['import_observatorio'](fake_zip(example_cartel()),'202610')
    assert report['processed']==1
    # Reimport same ZIP: one row per procedure; no lost client/date.
    namespace['import_observatorio'](fake_zip(example_cartel()),'202610')
    with engine.begin() as c:
        row=c.execute(text('SELECT * FROM '+CATALOG_FROM)).mappings().one()
        assert row['institution']=='Consejo de Transporte Público'
        assert row['publication_date']=='2026-10-15'
        assert row['closing_date']=='2026-10-31'
        assert row['status']=='Vigencia por verificar en SICOP'
        assert row['is_it']==1
        assert c.execute(text('SELECT COUNT(*) FROM observatory_tenders')).scalar_one()==1


def test_search_filter_soc_without_flask():
    """Test that the main profile and per-column area use the same SOC family."""
    from types import SimpleNamespace
    from app.technology import classified_sql, selected_filter, AREA_LABELS
    from app.publication import parse_publication_date
    tree=ast.parse((ROOT/'app/main.py').read_text(encoding='utf-8'))
    selected=[]
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(x,ast.Name) and x.id in ('COLUMN_FIELDS','COLUMN_FILTER_KEYS')
            for x in node.targets):selected.append(node)
        if isinstance(node,ast.FunctionDef) and node.name=='search_filter':selected.append(node)
    args = {'tech':'soc_related','column_area':'soc_related','source':'observatorio'}
    namespace = {
        'request':SimpleNamespace(args=SimpleNamespace(get=lambda key,default='':args.get(key,default))),
        'classified_sql':classified_sql,'selected_filter':selected_filter,
        'AREA_LABELS':AREA_LABELS,'parse_publication_date':parse_publication_date,
        'json':json, 'math':__import__('math'),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]),'<filter-function>','exec'),namespace)
    q,status,institution,tech,priority,cols,where,params,source=namespace['search_filter']()
    assert tech=='soc_related'
    assert source=='observatorio'
    assert 'soc_area_' in where
    assert "source_kind LIKE 'Observatorio%'" in where
    assert params['soc_area_0']
