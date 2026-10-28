"""Regression tests for Render psycopg StringDataRightTruncation (VARCHAR 140).

Run with: python -m pytest -q tests/test_v9_procedimiento_longitud.py
No external SICOP access or PostgreSQL credentials needed.
"""
import ast
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.record_quality import currency_code
from app.sicop_source import (canonical_procedure_number, parse_page,
                              SICOPSourceError)
from app.presentation import clean_description, buyer_name
from app.publication import date_from_row
from app.technology import classify_opportunity, CLASSIFICATION_VERSION
from tests.test_sicop_source import fixture_page

ROOT = Path(__file__).resolve().parents[1]
PROC = '2026LD-000099-0018300001'


def _isolated_upsert():
    """Execute the real upsert function without importing optional Flask."""
    source = ast.parse((ROOT / 'app/main.py').read_text(encoding='utf-8'))
    ddl = sql = None
    funcs = []
    for node in source.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if 'DDL' in names:
                ddl = ast.literal_eval(node.value)
            if 'UPSERT' in names:
                sql = node.value.args[0].value
        if isinstance(node, ast.FunctionDef) and node.name in ('value', 'number', 'upsert_rows'):
            funcs.append(node)
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        for stmt in ddl:
            conn.execute(text(stmt.replace('BIGSERIAL PRIMARY KEY',
                                           'INTEGER PRIMARY KEY AUTOINCREMENT')))
    namespace = {
        'engine': engine, 'UPSERT': text(sql), 'canonical_procedure_number': canonical_procedure_number,
        'SICOPSourceError': SICOPSourceError, 'SICOP_OPEN_DATA_URL': 'https://www.sicop.go.cr/test',
        'normalize': __import__('app.sicop_source', fromlist=['normalize']).normalize,
        'clean_description': clean_description, 'buyer_name': buyer_name,
        'date_from_row': date_from_row, 'classify_opportunity': classify_opportunity,
        'CLASSIFICATION_VERSION': CLASSIFICATION_VERSION,
        'utcnow': lambda: datetime.now(timezone.utc).isoformat(), 'json': json,
        'currency_code': currency_code,
    }
    exec(compile(ast.Module(body=funcs, type_ignores=[]), '<isolated app.main>', 'exec'), namespace)
    return namespace, engine


def test_single_identifier_survives_verbose_200_character_cell():
    raw = 'Número de procedimiento: ' + PROC + ' / ' + ('detalle de cartel ' * 13)
    assert len(raw) > 140
    assert canonical_procedure_number(raw) == PROC


def test_parser_normalizes_procedure_before_creating_sql_rows():
    html = fixture_page(first=99, count=1, total=1, pages=1)
    decorated = 'Nro. ' + PROC + ' ' + ('texto complementario ' * 12)
    html = html.replace("2026LD-000099-0000000001", decorated)
    page = parse_page(html)
    assert page.rows[0]['Número procedimiento'] == PROC
    assert page.rows[0]['Institución'] == 'Municipalidad'


def test_rejects_ambiguous_and_invalid_identifiers():
    assert canonical_procedure_number('sin número: ' + 'x'*190) == ''
    assert canonical_procedure_number(PROC + ' y 2026LE-000004-0008600001') == ''
    assert canonical_procedure_number('') == ''


def test_upsert_saves_canonical_number_from_verbose_input():
    ns, engine = _isolated_upsert()
    raw = 'Número de procedimiento ' + PROC + ' / ' + ('descripción adicional ' * 11)
    assert ns['upsert_rows']([{'Número procedimiento': raw, 'Descripción': 'SOC 24/7',
                               'Institución': 'JASEC', 'Moneda': 'CRC',
                               'Estado de procedimiento': 'En recepción de ofertas'}]) == 1
    with engine.begin() as conn:
        row = conn.execute(text('SELECT procedure_number,id,institution FROM tenders')).one()
    assert row.procedure_number == PROC
    assert row.id == PROC + '|0|0'
    assert row.institution == 'JASEC'


def test_upsert_refuses_garbage_instead_of_corrupting_db():
    ns, engine = _isolated_upsert()
    with pytest.raises(SICOPSourceError, match='Número de procedimiento inválido'):
        ns['upsert_rows']([{'Número procedimiento': 'ESTO NO ES UN ID ' + 'x'*170}])
    with engine.begin() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM tenders')).scalar_one() == 0


def test_field_length_problem_names_its_field_before_database_error():
    ns, _ = _isolated_upsert()
    with pytest.raises(SICOPSourceError, match='Moneda inconsistente'):
        ns['upsert_rows']([{'Número procedimiento': PROC, 'Moneda': 'CRC-'*70}])
    with pytest.raises(SICOPSourceError, match='Estado de procedimiento inconsistente'):
        ns['upsert_rows']([{'Número procedimiento': PROC,
                            'Estado de procedimiento': 'vigente'*80}])


def test_same_procedure_with_verbose_label_updates_instead_of_duplicating():
    ns, engine = _isolated_upsert()
    base = {'Número procedimiento': PROC, 'Descripción': 'Servicios SOC',
            'Institución': 'JASEC', 'Estado de procedimiento': 'Publicado'}
    assert ns['upsert_rows']([base]) == 1
    verbose = dict(base, **{'Número procedimiento':
                   'Número procedimiento: ' + PROC + ' — texto extra ' * 12})
    assert ns['upsert_rows']([verbose]) == 1
    with engine.begin() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM tenders')).scalar_one() == 1
        assert conn.execute(text('SELECT procedure_number FROM tenders')).scalar_one() == PROC


def test_ten_row_html_page_with_one_verbose_id_keeps_ten_valid_rows():
    html = fixture_page(first=1, count=10)
    raw = 'Número de procedimiento: 2026LD-000005-0000000001 ' + 'texto extra ' * 20
    html = html.replace('2026LD-000005-0000000001', raw)
    page = parse_page(html)
    assert len(page.rows) == 10
    assert page.rows[4]['Número procedimiento'] == '2026LD-000005-0000000001'
