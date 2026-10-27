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


def test_parser_accepts_extra_sort_icon_header_cell():
    """SICOP may have one extra header cell for sort/action controls."""
    html = fixture_page(count=2)
    html = html.replace('<th>No.</th>', '<th>Icono</th><th>No.</th>')
    page = parse_page(html)
    assert len(page.rows) == 2
    assert page.rows[0]['Número procedimiento'] == '2026LD-000001-0000000001'
    assert page.rows[0]['Institución'] == 'Municipalidad'


def test_parser_accepts_extra_leading_data_cell():
    html = fixture_page(count=2).replace(
        '<tr><td>1</td>', '<tr><td><img src="sort.png"></td><td>1</td>')
    page = parse_page(html)
    assert len(page.rows) == 2
    assert page.rows[0]['Descripción'] == 'Compra número 1'


def test_parser_falls_back_when_header_is_split():
    html = fixture_page(count=2).replace(
        '<th>Número procedimiento</th>', '<th>Número</th><th>procedimiento</th>')
    # The row still carries the report's normal 15 logical columns.
    page = parse_page(html)
    assert len(page.rows) == 2
    assert page.rows[0]['Número procedimiento'].startswith('2026LD-')


def test_parser_rejects_report_count_without_rows():
    with pytest.raises(SICOPSourceError, match='no se pudieron leer'):
        parse_page('<html><body>Datos abiertos concursos 23 resultados [ 1 de 3 páginas ]'
                   '<table><tr><th>Sin datos</th></tr></table></body></html>')


def test_get_page_rejects_wrong_page(monkeypatch):
    from app.sicop_source import get_page
    class FakeResponse:
        text = fixture_page(current=1)
        def raise_for_status(self):
            return None
    class FakeSession:
        def get(self, *args, **kwargs):
            return FakeResponse()
    with pytest.raises(SICOPSourceError, match='cuando se pidió la 2'):
        get_page(FakeSession(), 'https://www.sicop.go.cr/reporte', 2)


def test_fingerprint_includes_amount_not_only_identifiers():
    # A matching procedure/partida/linea/awardee is not enough to call a page a duplicate.
    first = fixture_page(count=2)
    second = first.replace('<td>1000</td>', '<td>1100</td>')
    assert parse_page(first).signature != parse_page(second).signature


def test_fingerprint_ignores_display_row_number():
    first = fixture_page(count=2)
    second = first.replace('<tr><td>1</td>', '<tr><td>500</td>')
    assert parse_page(first).signature == parse_page(second).signature


def test_get_page_retries_duplicate_then_accepts_new_content(monkeypatch):
    from app.sicop_source import get_page
    first = parse_page(fixture_page(first=1, count=2, current=3)).signature
    contents = [fixture_page(first=1, count=2, current=4),
                fixture_page(first=21, count=2, current=4)]
    calls = []

    class FakeResponse:
        def __init__(self, html):
            self.text = html
        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse(contents.pop(0))

    monkeypatch.setattr('app.sicop_source.time.sleep', lambda n: None)
    result = get_page(FakeSession(), 'https://www.sicop.go.cr/view', 4,
                      previous_signature=first)
    assert result.rows[0]['Número procedimiento'] == '2026LD-000021-0000000001'
    assert len(calls) == 2
    assert '_sicop_retry=' in calls[1][0]
    assert calls[1][1]['headers']['Cache-Control'].startswith('no-cache')


def test_get_page_stops_after_three_identical_pages(monkeypatch):
    from app.sicop_source import get_page
    first = parse_page(fixture_page(count=1, current=3)).signature
    calls = []

    class FakeResponse:
        text = fixture_page(count=1, current=4)
        def raise_for_status(self):
            pass
    class FakeSession:
        def get(self, url, **kwargs):
            calls.append(url)
            return FakeResponse()

    monkeypatch.setattr('app.sicop_source.time.sleep', lambda n: None)
    with pytest.raises(SICOPSourceError, match='incluso tras 3 consultas'):
        get_page(FakeSession(), 'https://www.sicop.go.cr/view', 4,
                 previous_signature=first)
    assert len(calls) == 3
