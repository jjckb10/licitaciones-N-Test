"""Run: python -m pytest -q tests/test_sicop_source.py"""
import pytest
from app.sicop_source import SICOPSourceError, normalize, paged_url, parse_page


def fixture_page(first=1, count=10, total=23, current=1, pages=3):
    rows = ''.join(
        f'<tr><td>{n}</td><td>Compra número {n}</td><td>2026LD-{n:06d}-0000000001</td>'
        f'<td>Municipalidad</td><td>Licitación reducida</td><td>BIENES</td>'
        f'<td>1</td><td>1</td><td>Proveedor {n}</td><td>CRC</td><td>1000</td>'
        f'<td>1</td><td>1000</td><td>En recepción de ofertas</td></tr>'
        for n in range(first, first+count))
    return f'''<html><body>Datos abiertos concursos
        <table><tr><th>No.</th><th>Descripción</th><th>Número procedimiento</th>
        <th>Institución</th><th>Tipo Procedimiento</th><th>Clasificación objeto</th>
        <th>Partida</th><th>Número de línea</th><th>Adjudicatario</th>
        <th>Moneda</th><th>Monto</th><th>Tipo de cambio</th>
        <th>Monto adjudicado en colones</th><th>Estado de procedimiento</th></tr>
        {rows}</table>{total} resultados [ {current} de {pages} páginas ]</body></html>'''


def test_parser_extracts_all_ten_records():
    page = parse_page(fixture_page())
    assert len(page.rows) == 10
    assert page.total_results == 23
    assert page.total_pages == 3
    assert page.rows[0]['Número procedimiento'].startswith('2026LD-')


def test_parser_handles_last_short_page():
    page = parse_page(fixture_page(first=21, count=3, current=3))
    assert len(page.rows) == 3
    assert page.page_number == 3


def test_sicop_url_preserves_existing_parameters():
    result = paged_url('https://sicop.example/view.jsp?date=2026&page_no=1', 5)
    assert result == 'https://sicop.example/view.jsp?date=2026&page_no=5'


def test_parser_rejects_error_page():
    with pytest.raises(SICOPSourceError):
        parse_page('<html>No fue posible acceder a la página solicitada. ID de soporte:</html>')


def test_parser_rejects_irrelevant_html():
    with pytest.raises(SICOPSourceError):
        parse_page('<html><table><tr><th>Hola</th></tr></table></html>')


def test_normalize_preserves_accented_column_meaning():
    assert normalize('Número de línea') == 'numero_de_linea'
