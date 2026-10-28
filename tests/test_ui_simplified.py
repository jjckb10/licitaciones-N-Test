"""V6.1: one source of truth for filters, compact table and grouped security."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import create_engine, text

from app.technology import AREA_LABELS, TECH_FILTER_OPTIONS, classified_sql, selected_filter

ROOT = Path(__file__).resolve().parents[1]


def render_dashboard(**changes):
    options = dict(
        url_for=lambda endpoint, **params: '/' + endpoint + (
            '?' + urlencode(params) if params else ''),
        get_flashed_messages=lambda **kwargs: [],
        u={'email': 'pruebas@ejemplo.com', 'admin': False},
        stats=SimpleNamespace(total=8, institutions=2, ti_total=5, altas=1),
        rows=[{'institution': 'ICE', 'procedure_number': '2026LD-000001-0000000001',
               'description': 'Licencias Microsoft 365', 'status': 'En recepción',
               'tech_areas': ['Microsoft'], 'summary': 'Renovar licencias',
               'publication_display': '28/10/2026', 'publication_date': '2026-10-28',
               'procedure_type': 'Licitación menor', 'it_score': 70,
               'it_priority': 'Alta', 'currency': 'CRC', 'amount': 1200,
               'awardee': ''}],
        q='', status='', inst='', tech='noventiq', priority='',
        sort='relevance', per_page=50, page=1, pages=2, page_numbers=[1, 2],
        matched=5, column={}, column_active=False, column_filter_count=0,
        active_chips=[],
        area_options=AREA_LABELS.items(), tech_filter_options=TECH_FILTER_OPTIONS,
        sort_options=[('relevance', 'Mayor relevancia')],
        query_args={'q': '', 'tech': 'noventiq', 'per_page': 50, 'sort': 'relevance'},
    )
    options.update(changes)
    template = Environment(loader=FileSystemLoader(str(ROOT/'app/templates'))).get_template('index.html')
    return template.render(**options)


def test_only_one_search_form_and_all_column_filters_associated():
    html = render_dashboard()
    soup = BeautifulSoup(html, 'html.parser')
    filters = soup.find('form', id='filters-form')
    assert filters and filters.get('method') == 'get'
    assert len(soup.find_all('form')) == 1
    for name in ('client_contains', 'description_contains', 'published_from',
                 'published_to', 'publication_missing', 'column_priority',
                 'column_area', 'procedure_contains', 'state_contains',
                 'type_contains', 'amount_min', 'amount_max', 'awardee_contains'):
        control = soup.find(attrs={'name': name})
        assert control is not None, name
        assert control['form'] == 'filters-form', name
    for name in ('status', 'institution', 'priority'):
        assert soup.find(attrs={'name': name}) is None, f'duplicate global control: {name}'
    assert soup.find(id='column-filter-row').has_attr('hidden')
    assert soup.find(id='toggle-columns')['aria-expanded'] == 'false'


def test_existing_column_filters_open_row_and_navigation_preserves_all():
    html = render_dashboard(
        q='Microsoft', tech='security_all',
        column={'client_contains': 'ICE', 'published_from': '2026-10-01'},
        column_active=True, column_filter_count=2,
        query_args={'q': 'Microsoft', 'tech': 'security_all',
                    'client_contains': 'ICE', 'published_from': '2026-10-01',
                    'per_page': 50, 'sort': 'relevance'},
    )
    soup = BeautifulSoup(html, 'html.parser')
    assert not soup.find(id='column-filter-row').has_attr('hidden')
    assert soup.find(id='toggle-columns')['aria-expanded'] == 'true'
    assert soup.find(attrs={'name': 'client_contains'})['value'] == 'ICE'
    assert soup.find(attrs={'name': 'published_from'})['value'] == '2026-10-01'
    assert 'client_contains=ICE' in soup.find('a', string='Siguiente')['href']
    assert 'published_from=2026-10-01' in soup.find('a', string='Exportar CSV')['href']
    assert 'tech=security_all' in soup.find('a', string='Exportar CSV')['href']


def test_general_security_covers_all_subcategories_without_other_purchases():
    assert selected_filter('security_all') == 'security_all'
    where, params = classified_sql('security_all')
    assert ' OR ' in where
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE tenders (id INTEGER PRIMARY KEY,it_labels TEXT)'))
        conn.execute(text('INSERT INTO tenders(id,it_labels) VALUES (1,:soc),(2,:iam),(3,:unrelated)'), {
            'soc': json.dumps([AREA_LABELS['soc']], ensure_ascii=False),
            'iam': json.dumps([AREA_LABELS['iam']], ensure_ascii=False),
            'unrelated': json.dumps([AREA_LABELS['hardware']], ensure_ascii=False),
        })
        got = [r[0] for r in conn.execute(text('SELECT id FROM tenders WHERE '+where+' ORDER BY id'), params)]
    assert got == [1, 2]


def test_ui_filter_chips_remove_only_chosen_filter():
    code = ast.parse((ROOT/'app/main.py').read_text())
    nodes = [node for node in code.body if
             (isinstance(node, ast.Assign) and any(
                  isinstance(t, ast.Name) and t.id=='FILTER_DESCRIPTIONS' for t in node.targets))
             or (isinstance(node, ast.FunctionDef) and node.name=='describe_filters')]
    namespace={'TECH_FILTER_OPTIONS':TECH_FILTER_OPTIONS,
               'url_for':lambda endpoint, **params: '/'+endpoint+'?'+urlencode(params)}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<test>','exec'), namespace)
    chips = namespace['describe_filters']({
        'q':'servidores', 'tech':'security_all', 'client_contains':'ICE', 'per_page':50})
    assert len(chips) == 3
    client_chip = next(c for c in chips if c['label']=='Cliente')
    assert 'q=servidores' in client_chip['url']
    assert 'tech=security_all' in client_chip['url']
    assert 'client_contains=' in client_chip['url']


def test_admin_actions_remain_accessible_in_collapsed_panel():
    html = render_dashboard(
        u={'email':'administrador@ejemplo.com','admin':True},
        sync_state=SimpleNamespace(next_page=2,total_pages=4,total_results=40),
        last_run=None, csrf_token='test-token')
    soup = BeautifulSoup(html,'html.parser')
    panel = soup.find('section',class_='admin-tools')
    assert panel.find('details') and panel.find('summary')
    assert soup.find('a',href='/manage_users')
    for endpoint in ('/manual_sync','/reset_sync','/upload_csv'):
        assert soup.find('form',action=endpoint)
    assert html.count('name="csrf_token"') == 3
    assert 'X-CSRF-Token' in html
