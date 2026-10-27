"""Offline SQL checks, including a V3 -> V4 SQLite schema migration scenario.

Do not require Flask, Render, or an external SICOP connection.
"""
import ast
from pathlib import Path
import json
from sqlalchemy import create_engine, inspect, text
from app.technology import classify_opportunity, classified_sql


def source_statements():
    path = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
    tree = ast.parse(path.read_text())
    ddl = upsert = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if 'DDL' in names:
                ddl = ast.literal_eval(node.value)
            if 'UPSERT' in names:
                upsert = node.value.args[0].value
    return ddl, upsert


def test_upsert_and_tech_filter_sql_valid_in_sqlite():
    ddl, upsert = source_statements()
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        for statement in ddl:
            conn.execute(text(statement.replace('BIGSERIAL PRIMARY KEY',
                                                'INTEGER PRIMARY KEY AUTOINCREMENT')))
        row = {
            'id': '2026LD-000001-0000100001|1|1',
            'procedure_number': '2026LD-000001-0000100001',
            'description': 'Licenciamiento Microsoft 365 y Azure',
            'institution': 'INSTITUTO COSTARRICENSE DE ELECTRICIDAD',
            'procedure_type': 'LICITACIÓN MENOR', 'modality': 'Según demanda',
            'object_classification': 'SERVICIOS', 'item': '1',
            'line_number': '1', 'awardee': '', 'currency': 'CRC',
            'amount': None, 'exchange_rate': None, 'awarded_crc': None,
            'status': 'En recepción de ofertas', 'source_url': 'https://sicop.go.cr',
            'first_seen': '2026-10-27', 'last_seen': '2026-10-27',
        }
        classification = classify_opportunity(row['description'])
        row.update({
            'is_it': classification['is_it'], 'it_score': classification['it_score'],
            'it_priority': classification['it_priority'],
            'it_labels': json.dumps(classification['it_labels'], ensure_ascii=False),
        })
        conn.execute(text(upsert), row)
        # Bad subsequent parsed row must not corrupt a good buyer or rating.
        bad = dict(row, institution='', description='', is_it=0, it_score=0,
                   it_priority='No aplica', it_labels='[]')
        conn.execute(text(upsert), bad)
        existing = conn.execute(text('SELECT * FROM tenders')).mappings().one()
        assert existing['institution'] == 'INSTITUTO COSTARRICENSE DE ELECTRICIDAD'
        assert existing['description'] == 'Licenciamiento Microsoft 365 y Azure'
        assert existing['it_priority'] == 'Alta'
        where, params = classified_sql('noventiq')
        assert conn.execute(text(f'SELECT COUNT(*) FROM tenders WHERE {where}'), params).scalar() == 1
        where, params = classified_sql('microsoft')
        assert conn.execute(text(f'SELECT COUNT(*) FROM tenders WHERE {where}'), params).scalar() == 1


def test_old_sqlite_table_can_be_extended_without_dropping_existing_records():
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE tenders(id VARCHAR(220) PRIMARY KEY, '
                          'description TEXT, institution TEXT, object_classification TEXT)'))
        conn.execute(text("INSERT INTO tenders VALUES ('test-1','Azure','LICITACIÓN REDUCIDA','SERVICIOS')"))
        have = {c['name'] for c in inspect(conn).get_columns('tenders')}
        for name, kind in [('is_it','INTEGER'), ('it_score','INTEGER'),
                           ('it_priority','VARCHAR(20)'), ('it_labels','TEXT')]:
            if name not in have:
                conn.execute(text(f'ALTER TABLE tenders ADD COLUMN {name} {kind}'))
        assert conn.execute(text('SELECT COUNT(*) FROM tenders')).scalar() == 1
        assert 'it_score' in {c['name'] for c in inspect(conn).get_columns('tenders')}
