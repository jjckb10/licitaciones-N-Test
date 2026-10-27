"""Technology opportunity filtering should be consistent in SQLite and Postgres."""
from sqlalchemy import create_engine, text

from app.technology import (TECH_AREAS, TECH_FILTER_OPTIONS, matched_areas,
                            selected_filter, technology_sql)

EXAMPLES = (
    (1, 'Renovación licenciamiento Microsoft 365', 'Servicios', 'microsoft'),
    (2, 'Migración de infraestructura a Azure Cloud', 'Servicios', 'cloud'),
    (3, 'Servicios cloud Amazon Web Services AWS', 'Servicios', 'aws'),
    (4, 'Renovación Google Workspace para usuarios', 'Servicios', 'google'),
    (5, 'Compra de laptops y servidores', 'Equipo de cómputo', 'hardware'),
    (6, 'Monitoreo de ciberseguridad mediante SOC y SIEM', 'Servicios', 'cybersecurity'),
    (7, 'Compra de agua embotellada y artículos de limpieza', 'Suministros', None),
    (8, 'Servicios de mantenimiento de parques y jardines', 'Servicios', None),
    (9, 'Desarrollo de aplicación web institucional', 'Servicios', 'software'),
)


def matching_ids(engine, category):
    where, params = technology_sql(category)
    with engine.connect() as conn:
        q = 'SELECT id FROM tenders' + (f' WHERE {where}' if where else '') + ' ORDER BY id'
        return [row[0] for row in conn.execute(text(q), params)]


def test_filter_categories_return_only_relevant_rows():
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE tenders (id INT, description TEXT, object_classification TEXT)'))
        conn.execute(text('INSERT INTO tenders(id, description, object_classification) '
                          'VALUES (:id, :description, :classification)'), [
            {'id': id_, 'description': desc, 'classification': kind}
            for id_, desc, kind, _ in EXAMPLES
        ])
    assert matching_ids(engine, 'all') == list(range(1, 10))
    assert matching_ids(engine, 'noventiq') == [1, 2, 3, 4, 5, 6, 9]
    for _, _, _, category in EXAMPLES:
        if category:
            assert matching_ids(engine, category)
    assert 7 not in matching_ids(engine, 'microsoft')
    assert 8 not in matching_ids(engine, 'noventiq')
    assert 5 in matching_ids(engine, 'hardware')


def test_filter_options_whitelist_and_parameters():
    assert selected_filter('random') == 'all'
    assert technology_sql('random') == ('', {})
    clause, params = technology_sql('microsoft')
    assert 'LIKE :tech_' in clause
    assert all(v.startswith('%') and v.endswith('%') for v in params.values())
    assert 'microsoft' in dict(TECH_FILTER_OPTIONS)
    assert set(TECH_AREAS).issubset(dict(TECH_FILTER_OPTIONS))


def test_matched_category_labels_and_no_buyer_or_provider_scoring():
    assert matched_areas('Servicios de AWS cloud')
    assert not matched_areas('Compra de refrescos')
    assert not matched_areas('', '')


def test_technology_filters_are_visible_and_navigation_preserves_selection():
    from pathlib import Path
    from types import SimpleNamespace
    from jinja2 import Environment, FileSystemLoader

    root = Path(__file__).resolve().parents[1]
    template = Environment(loader=FileSystemLoader(root / 'app' / 'templates')).get_template('index.html')

    def route(name, **params):
        from urllib.parse import urlencode
        return '/' + name + ('?' + urlencode(params) if params else '')

    page = template.render(
        url_for=route, get_flashed_messages=lambda **kwargs: [],
        u={'email': 'test@ejemplo.com', 'admin': False},
        stats=SimpleNamespace(total=25, institutions=2),
        rows=[{'procedure_number': '2026LD-000001-0000000001',
               'institution': 'Institución pública',
               'description': 'Renovación Microsoft 365',
               'tech_areas': matched_areas('Renovación Microsoft 365'),
               'object_classification': 'Servicios', 'status': 'En recepción de ofertas',
               'currency': 'CRC', 'amount': 1500.0, 'awardee': ''}],
        statuses=['En recepción de ofertas'], institutions=['Institución pública'],
        q='', status='', inst='', tech='microsoft',
        tech_filter_options=TECH_FILTER_OPTIONS,
        page=2, pages=3, page_numbers=[1, 2, 3], per_page=50, matched=120,
    )
    assert 'Ver solo oportunidades TI (Noventiq)' in page
    assert 'Microsoft / Microsoft 365 / Azure' in page
    assert 'Área tecnológica detectada' in page
    assert 'tech=microsoft' in page   # Pagination and CSV must retain this selection.
    assert '/export_csv?' in page and 'institution=&tech=microsoft' in page
