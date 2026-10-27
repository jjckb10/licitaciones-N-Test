"""Verify the public dashboard names the procuring client clearly."""
from pathlib import Path
from types import SimpleNamespace

import pytest

jinja2 = pytest.importorskip('jinja2')


def test_tender_list_displays_buyer_before_supplier():
    root = Path(__file__).resolve().parents[1]
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(root / 'app' / 'templates'),
        autoescape=True,
    )
    template = environment.get_template('index.html')
    html = template.render(
        url_for=lambda name, **kwargs: '/' + name,
        get_flashed_messages=lambda **kwargs: [],
        u={'email': 'example@example.test', 'admin': False},
        stats=SimpleNamespace(total=1, institutions=1),
        rows=[{'procedure_number': '2026LD-000001-0000000001',
               'institution': 'MUNICIPALIDAD DE SAN JOSÉ',
               'description': 'Servicio de mantenimiento de equipos',
               'status': 'En recepción de ofertas',
               'currency': 'CRC', 'amount': 5000,
               'awardee': 'PROVEEDOR EJEMPLO'}],
        statuses=['En recepción de ofertas'],
        institutions=['MUNICIPALIDAD DE SAN JOSÉ'],
        q='', status='', inst='', page=1, pages=1, page_numbers=[1],
        per_page=50, matched=1,
    )
    assert 'Cliente / institución solicitante' in html
    assert 'MUNICIPALIDAD DE SAN JOSÉ' in html
    assert html.index('MUNICIPALIDAD DE SAN JOSÉ', html.index('<tbody>')) < html.index('PROVEEDOR EJEMPLO')
    assert 'Proveedor adjudicado' in html


def test_missing_client_is_not_misidentified_as_supplier():
    root = Path(__file__).resolve().parents[1]
    template = jinja2.Environment(loader=jinja2.FileSystemLoader(
        root / 'app' / 'templates')).get_template('index.html')
    html = template.render(
        url_for=lambda name, **kwargs: '/' + name,
        get_flashed_messages=lambda **kwargs: [],
        u={'email': 'test', 'admin': False},
        stats=SimpleNamespace(total=1, institutions=0),
        rows=[{'procedure_number': '2026LD-000001-0000000001',
               'institution': None, 'description': 'Limpieza',
               'status': '', 'currency': '', 'amount': None,
               'awardee': 'PROVEEDOR'}],
        statuses=[], institutions=[], q='', status='', inst='',
        page=1, pages=1, page_numbers=[1], per_page=50, matched=1,
    )
    assert 'Cliente no informado' in html
    assert 'PROVEEDOR' in html
