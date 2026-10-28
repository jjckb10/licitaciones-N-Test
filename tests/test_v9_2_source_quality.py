"""Regression tests for Render page 47 'Moneda' and legacy VARCHAR(140)."""
import ast
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

from sqlalchemy import create_engine, text, String, Text

from app.record_quality import currency_code
from app.sicop_source import parse_page
from tests.test_sicop_source import fixture_page
from tests.test_v9_procedimiento_longitud import _isolated_upsert

ROOT = Path(__file__).resolve().parents[1]
PROC = '2023LY-000021-0020600001'


def test_known_currency_names_normalize_without_guessing():
    assert currency_code('CRC') == 'CRC'
    assert currency_code('colones') == 'CRC'
    assert currency_code('Dólares') == 'USD'
    assert currency_code('US$') == 'USD'
    assert currency_code('EUR') == 'EUR'
    assert currency_code('') == ''
    assert currency_code('El servicio será adjudicado según disponibilidad y demanda') is None
    assert currency_code('1500000') is None


def test_sicop_header_has_modality_but_actual_data_row_omits_it():
    # The exact variant that makes all columns after modality shift one place.
    html = fixture_page(first=1,count=1,total=1,pages=1).replace(
        '<th>Clasificación objeto</th>',
        '<th>Tipo modalidad</th><th>Clasificación objeto</th>')
    page = parse_page(html)
    assert len(page.rows) == 1
    item = page.rows[0]
    assert item['Número procedimiento'] == '2026LD-000001-0000000001'
    assert item['Institución'] == 'Municipalidad'
    assert item['Clasificación objeto'] == 'BIENES'
    assert item['Moneda'] == 'CRC'
    assert item['Monto'] == '1000'
    assert item['Estado de procedimiento'] == 'En recepción de ofertas'
    assert 'Tipo modalidad' not in item


def test_sicop_header_and_actual_row_both_have_modality():
    html = fixture_page(first=1,count=1,total=1,pages=1).replace(
        '<th>Clasificación objeto</th>',
        '<th>Tipo modalidad</th><th>Clasificación objeto</th>')
    html = html.replace('<td>Licitación reducida</td><td>BIENES</td>',
                        '<td>Licitación reducida</td><td>Según demanda</td><td>BIENES</td>')
    row = parse_page(html).rows[0]
    assert row['Tipo modalidad'] == 'Según demanda'
    assert row['Moneda'] == 'CRC'
    assert row['Estado de procedimiento'] == 'En recepción de ofertas'


def test_one_malformed_currency_does_not_rollback_other_procurements():
    ns, engine = _isolated_upsert()
    good = {'Número procedimiento': PROC, 'Descripción': 'Monitoreo SOC',
            'Institución': 'Institución 1', 'Moneda': 'CRC',
            'Estado de procedimiento': 'En recepción de ofertas'}
    bad = {'Número procedimiento': '2026LE-000004-0008600001',
           'Descripción': 'Servicio de vigilancia de infraestructura',
           'Moneda': 'Informacion de la entidad compradora y servicio ' * 8,
           'Estado de procedimiento': 'En recepción de ofertas'}
    issues = []
    with engine.begin() as conn:
        saved = ns['upsert_rows']([good,bad],conn,issues=issues)
        assert saved == 1
        assert len(issues) == 1
        # The exact SQL and helper used by the production importer.
        tree = ast.parse((ROOT/'app/main.py').read_text(encoding='utf-8'))
        fn = next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                  and n.name == 'store_sicop_issues')
        env = {'text':text,'hashlib':hashlib,'json':json,
               'utcnow':lambda:datetime.now(timezone.utc).isoformat()}
        exec(compile(ast.Module(body=[fn], type_ignores=[]),'<source>', 'exec'),env)
        assert env['store_sicop_issues'](conn,47,issues) == 1
    with engine.begin() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM tenders')).scalar_one() == 1
        stored = conn.execute(text('SELECT page_number,procedure_number,raw_row FROM sicop_row_issues')).one()
        assert stored.page_number == 47
        assert stored.procedure_number == bad['Número procedimiento']
        assert json.loads(stored.raw_row)['Moneda'] == bad['Moneda']
        # Repeated attempt updates the issue, not duplicate rows.
        env['store_sicop_issues'](conn,47,issues)
        assert conn.execute(text('SELECT COUNT(*) FROM sicop_row_issues')).scalar_one() == 1
        assert conn.execute(text('SELECT attempts FROM sicop_row_issues')).scalar_one() == 2


def test_legacy_postgres_schema_widens_only_safe_text_fields():
    tree = ast.parse((ROOT/'app/main.py').read_text(encoding='utf-8'))
    fields = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t,ast.Name) and t.id == '_LEGACY_TEXT_FIELDS'
                          for t in n.targets))
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == 'widen_legacy_text_columns')
    class FakeInspect:
        def get_columns(self, table):
            if table == 'tenders':
                return [{'name':'institution','type':String(140)},
                        {'name':'procedure_number','type':String(140)},
                        {'name':'currency','type':String(20)},
                        {'name':'description','type':Text()},
                        {'name':'status','type':String(180)}]
            return [{'name':'description','type':String(140)}]
    class FakeConn:
        dialect = type('D',(),{'name':'postgresql'})()
        def __init__(self): self.statements=[]
        def execute(self,sql): self.statements.append(str(sql))
    env={'String':String,'text':text,'inspect':lambda conn:FakeInspect()}
    # Evaluate source-defined allowlist (no local copy) then function.
    assign=next(n for n in tree.body if isinstance(n,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id == '_LEGACY_TEXT_FIELDS'
                        for t in n.targets))
    exec(compile(ast.Module(body=[assign,func],type_ignores=[]),'<schema>', 'exec'),env)
    conn=FakeConn()
    assert env['widen_legacy_text_columns'](conn)==3
    ddl=';'.join(conn.statements)
    assert 'tenders ALTER COLUMN institution TYPE TEXT' in ddl
    assert 'tenders ALTER COLUMN status TYPE TEXT' in ddl
    assert 'observatory_tenders ALTER COLUMN description TYPE TEXT' in ddl
    assert 'procedure_number' not in ddl and 'currency' not in ddl
    assert 'DROP' not in ddl and 'TRUNCATE' not in ddl
