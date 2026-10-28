"""V5: dates, security categories, column filters, and preserving legacy data."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import create_engine, inspect, text

from app.publication import date_from_row, parse_publication_date, display_publication_date
from app.technology import (TECH_FILTER_OPTIONS, classify_opportunity, AREA_LABELS,
                            selected_filter, classified_sql, CLASSIFICATION_VERSION)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('field,value,expected', [
    ('Fecha de publicación', '27/10/2026 09:30', '2026-10-27'),
    ('Fecha/hora de publicación', '2026-10-28 18:30:00', '2026-10-28'),
    ('FECHAH_PUBLICACION', '2026-10-29T10:15:00Z', '2026-10-29'),
    ('publicationDate', '2026-09-30', '2026-09-30'),
    ('Fecha publicación', '31/02/2026', None),
])
def test_explicit_source_date_parsing(field, value, expected):
    assert date_from_row({field: value}) == expected


def test_no_date_is_derived_from_import_date_or_procedure_number():
    assert date_from_row({'Número procedimiento': '2026LD-000012-0000000001',
                          'first_seen': '2026-10-28T12:00:00+00:00'}) is None
    assert parse_publication_date('2026-12-37') is None
    assert display_publication_date(None) == 'No disponible'


@pytest.mark.parametrize('value,category', [
    ('Contratación de SOC 24x7 y servicios MDR', 'soc'),
    ('Implementación de plataforma SIEM y SOAR', 'siem'),
    ('Operaciones de seguridad y respuesta a incidentes', 'security_operations'),
    ('Gestión de accesos privilegiados PAM', 'iam'),
    ('Servicio de seguridad administrada MSSP', 'managed_security'),
    ('Evaluación de vulnerabilidades y pentest', 'vulnerability_management'),
    ('Implementación de SGSI ISO 27001', 'security_governance'),
])
def test_new_security_classifications(value, category):
    result = classify_opportunity(value)
    assert result['is_it'] == 1
    assert AREA_LABELS[category] in result['it_labels']
    assert category in dict(TECH_FILTER_OPTIONS)


def test_soc_does_not_match_sociedad():
    assert classify_opportunity('Asesoría sobre sociedad civil y trabajo social')['is_it'] == 0


def source_sql():
    tree = ast.parse((ROOT / 'app/main.py').read_text(encoding='utf-8'))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for name in node.targets:
                if isinstance(name, ast.Name) and name.id == 'DDL':
                    values['ddl'] = ast.literal_eval(node.value)
                if isinstance(name, ast.Name) and name.id == 'UPSERT':
                    values['upsert'] = node.value.args[0].value
    return values['ddl'], values['upsert']


def test_upsert_keeps_known_publication_date_when_next_report_omits_it():
    ddl, upsert = source_sql()
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        for statement in ddl:
            conn.execute(text(statement.replace('BIGSERIAL PRIMARY KEY',
                                                'INTEGER PRIMARY KEY AUTOINCREMENT')))
        template = {name: None for name in text(upsert)._bindparams}
        template.update({'id': '2026LD-000099-0000010001|1|1',
                         'procedure_number': '2026LD-000099-0000010001',
                         'institution': 'ICE', 'description': 'Centro de operaciones SOC',
                         'publication_date': '2026-10-25',
                         'it_priority': 'Alta', 'is_it': 1, 'it_score': 100,
                         'it_labels': json.dumps(['SOC · Centro de Operaciones de Seguridad']),
                         'classification_version': CLASSIFICATION_VERSION,
                         'first_seen': '2026-10-28', 'last_seen': '2026-10-28'})
        conn.execute(text(upsert), template)
        row = dict(template, publication_date=None, last_seen='2026-10-29')
        conn.execute(text(upsert), row)
        actual = conn.execute(text('SELECT publication_date, classification_version FROM tenders')).one()
        assert actual == ('2026-10-25', CLASSIFICATION_VERSION)


def test_date_and_category_filters_are_portable_sql():
    ddl, upsert = source_sql()
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        for statement in ddl:
            conn.execute(text(statement.replace('BIGSERIAL PRIMARY KEY',
                                                'INTEGER PRIMARY KEY AUTOINCREMENT')))
        conn.execute(text('''INSERT INTO tenders(id,description,publication_date,amount,it_labels,is_it)
            VALUES ('1','SOC de seguridad','2026-10-18',3500,:labels,1),
                   ('2','Mantenimiento jardines',NULL,200,'[]',0)'''),
                     {'labels':json.dumps([AREA_LABELS['soc']], ensure_ascii=False)})
        pred, params = classified_sql('soc')
        sql = ('SELECT id FROM tenders WHERE ' + pred +
               ' AND publication_date >= :published_from AND amount <= :max_amount')
        matches = conn.execute(text(sql), dict(params, published_from='2026-10-01',max_amount=4000)).all()
        assert matches == [('1',)]
        assert conn.execute(text('SELECT COUNT(*) FROM tenders WHERE publication_date IS NULL')).scalar() == 1


def test_template_has_column_filters_and_date_and_export():
    source = (ROOT/'app/templates/index.html').read_text()
    for field in ('client_contains','description_contains','published_from',
                  'published_to','column_priority','column_area','procedure_contains',
                  'state_contains','type_contains','amount_min','amount_max',
                  'awardee_contains'):
        assert f'name="{field}"' in source
    template = Environment(loader=FileSystemLoader(ROOT/'app/templates')).get_template('index.html')
    # The template uses an ordinary test-friendly url_for function.
    rendered = template.render(url_for=lambda name, **kwargs: '/'+name,
        get_flashed_messages=lambda **kwargs: [],
        u={'email':'test','admin':False}, stats=SimpleNamespace(total=1, institutions=1),
        statuses=[], institutions=[], q='', status='', inst='', tech='noventiq',
        priority='', per_page=50, page=1, pages=1, page_numbers=[1], matched=1,
        tech_filter_options=TECH_FILTER_OPTIONS, area_options=AREA_LABELS.items(),
        sort_options=[('relevance','Mayor relevancia')],sort='relevance',
        column={'client_contains':'ICE', 'published_from':'2026-10-01'},
        query_args={'tech':'noventiq'},
        rows=[{'client_display':'ICE','summary':'Servicio de SOC','publication_display':'18/10/2026',
               'publication_date':'2026-10-18','tech_areas':[AREA_LABELS['soc']],
               'procedure_number':'2026LD-000001-0000000001','it_priority':'Alta','it_score':100,
               'status':'En recepción','procedure_type':'Licitación menor','currency':'CRC',
               'amount':1000,'awardee':''}])
    assert '18/10/2026' in rendered
    assert 'Soc' in rendered or 'SOC' in rendered
    assert 'name="published_from"' in rendered
    assert 'value="ICE"' in rendered
    assert '/export_csv' in rendered


def test_column_filters_query_entire_database_and_reject_invalid_dates():
    """Compile the real view filter without Flask, then query SQLite."""
    from app.technology import selected_filter, classified_sql
    tree = ast.parse((ROOT/'app/main.py').read_text(encoding='utf-8'))
    wanted = ('COLUMN_FIELDS', 'COLUMN_FILTER_KEYS', 'search_filter')
    nodes = [node for node in tree.body if
             (isinstance(node, ast.Assign) and any(
                 isinstance(n, ast.Name) and n.id in wanted for n in node.targets))
             or (isinstance(node, ast.FunctionDef) and node.name == 'search_filter')]
    args = {'tech':'all', 'client_contains':'instituto',
            'description_contains':'SOC', 'column_area':'soc',
            'published_from':'2026-10-01', 'published_to':'2026-10-31',
            'amount_min':'100', 'state_contains':'recepción'}
    request = SimpleNamespace(args=SimpleNamespace(
        get=lambda key, default='': args.get(key, default)))
    namespace = dict(request=request, selected_filter=selected_filter,
                     classified_sql=classified_sql, json=json,
                     parse_publication_date=parse_publication_date,
                     AREA_LABELS=AREA_LABELS, math=__import__('math'))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<filter>', 'exec'), namespace)
    q, status, institution, tech, priority, cols, where, params, source_filter = namespace['search_filter']()
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('''CREATE TABLE tenders (id TEXT PRIMARY KEY, description TEXT,
            institution TEXT,procedure_number TEXT, status TEXT,procedure_type TEXT,
            awardee TEXT,it_priority TEXT,it_labels TEXT,object_classification TEXT,
            is_it INTEGER,publication_date TEXT,amount REAL)'''))
        conn.execute(text('''INSERT INTO tenders VALUES
        ('1','Servicios SOC','Instituto estatal','2026LD-000001','En recepción',
         'Licitación menor','', 'Alta',:labels,'Servicios',1,'2026-10-12',1500),
        ('2','Servicios SOC','Instituto estatal','2026LD-000002','En recepción',
         'Licitación menor','', 'Alta',:labels,'Servicios',1,NULL,1800),
        ('3','Servicios SOC','Empresa diferente','2026LD-000003','En recepción',
         'Licitación menor','', 'Alta',:labels,'Servicios',1,'2026-10-20',1300)'''),
            {'labels':json.dumps([AREA_LABELS['soc']], ensure_ascii=False)})
        matched = conn.execute(text('SELECT id FROM tenders'+where),params).all()
        assert matched == [('1',)]
    args['published_from'] = '2026-13-38'
    _, _, _, _, _, cols, where, params, source_filter = namespace['search_filter']()
    assert cols['published_from'] == ''
    assert 'published_from' not in params
