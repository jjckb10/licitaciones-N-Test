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

    def fetch(_session, _url, number):
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
