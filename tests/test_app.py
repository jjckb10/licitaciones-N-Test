"""Integration tests; require the same dependencies as requirements.txt.

Run with: pip install -r requirements.txt pytest && pytest -q
"""
import importlib
import os

import pytest

pytest.importorskip('flask')
from tests.test_sicop_source import fixture_page
from app.sicop_source import parse_page


def test_paginated_import_resumes_after_restart(monkeypatch, tmp_path):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path}/test.db')
    monkeypatch.setenv('ADMIN_PASSWORD_1', 'test-password')
    main = importlib.import_module('app.main')
    # In normal deployment main is imported once per gunicorn worker.
    main.engine.dispose()
    from sqlalchemy import create_engine
    main.engine = create_engine(f'sqlite:///{tmp_path}/test.db')
    main.init_db()
    main.PAGES_PER_RUN = 2

    class FakeSession:
        def close(self):
            pass

    def fetch(_session, _url, number, previous_signature=None):
        start, count = ((1, 10) if number == 1 else (11, 10) if number == 2 else (21, 3))
        return parse_page(fixture_page(start, count, current=number))

    monkeypatch.setattr(main, 'get_page', fetch)
    monkeypatch.setattr(main, 'make_session', FakeSession)
    first = main.refresh_public_page()
    assert first['status'] == 'partial'
    assert first['processed'] == 20
    assert first['next_page'] == 3
    second = main.refresh_public_page()
    assert second['status'] == 'complete'
    assert second['stored'] == 23
    assert second['next_page'] == 1
    third = main.refresh_public_page()
    assert third['stored'] == 23  # Upsert; no duplicates.


def test_buyer_alias_is_saved_as_client_institution(monkeypatch, tmp_path):
    """SICOP may call the procuring organization Cliente/Entidad contratante."""
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path}/buyer.db')
    main = importlib.import_module('app.main')
    main.engine.dispose()
    from sqlalchemy import create_engine
    from sqlalchemy import text
    main.engine = create_engine(f'sqlite:///{tmp_path}/buyer.db')
    main.init_db()
    saved = main.upsert_rows([
        {'Número procedimiento': '2026LD-000001-0000000001',
         'Entidad contratante': 'Municipalidad de San José',
         'Descripción': 'Servicio de mantenimiento'},
        {'Número procedimiento': '2026LD-000002-0000000001',
         'Cliente': 'Instituto Costarricense de Electricidad',
         'Descripción': 'Servicios de tecnología'},
    ])
    assert saved == 2
    with main.engine.begin() as conn:
        clients = [row[0] for row in conn.execute(
            text('SELECT institution FROM tenders ORDER BY procedure_number'))]
    assert clients == ['Municipalidad de San José',
                       'Instituto Costarricense de Electricidad']
