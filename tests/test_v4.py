"""V4: buyer, short description, alternating sort headings, scoring, filters."""
import json

from app.presentation import buyer_name, clean_description, brief_description
from app.sicop_source import parse_page
from app.technology import classify_opportunity, classified_sql


def test_buyer_is_not_procurement_type():
    assert buyer_name('LICITACIÓN REDUCIDA') == ''
    assert buyer_name('PROCEDIMIENTOS ESPECIALES') == ''
    assert buyer_name('INSTITUTO COSTARRICENSE DE ELECTRICIDAD') == 'INSTITUTO COSTARRICENSE DE ELECTRICIDAD'
    assert clean_description('LICITACIÓN REDUCIDA') == ''
    assert brief_description('') == 'Descripción no disponible en la fuente'
    assert brief_description('Adquisición de licencias Microsoft 365') == 'Adquisición de licencias Microsoft 365'


def test_licitacion_summary_is_bounded_and_truthful():
    txt = 'Renovación de licencias Microsoft 365 para instituciones públicas ' * 6
    result = brief_description(txt, limit=140)
    assert len(result) <= 140
    assert result.startswith('Renovación de licencias') and result.endswith('…')


def test_public_header_sort_images_do_not_shift_buyer():
    names = ['No.', 'Descripción', 'Número procedimiento', 'Institución',
             'Tipo Procedimiento', 'Tipo modalidad', 'Clasificación objeto',
             'Partida', 'Número de línea', 'Adjudicatario', 'Moneda',
             'Monto', 'Tipo de cambio', 'Monto adjudicado en colones',
             'Estado de procedimiento']
    values = ['1', 'Licencias Microsoft Azure', '2026LD-000001-0000100001',
              'MINISTERIO DE EDUCACIÓN PÚBLICA', 'LICITACIÓN REDUCIDA',
              'Cantidad definida', 'SERVICIOS', '1', '1', 'PROVEEDOR EJEMPLO',
              'CRC', '5000000', '1', '5000000', 'En recepción de ofertas']
    heads = ''.join(f'<th><img src="sort.png"/></th><th>{n}</th>' for n in names)
    cells = ''.join(f'<td>{v}</td>' for v in values)
    page = parse_page(f'<html><body><table><tr>{heads}</tr><tr>{cells}</tr></table>'
                      '20 resultados [ 1 de 2 páginas ]</body></html>')
    assert page.rows[0]['Institución'] == 'MINISTERIO DE EDUCACIÓN PÚBLICA'
    assert page.rows[0]['Tipo Procedimiento'] == 'LICITACIÓN REDUCIDA'
    assert page.rows[0]['Descripción'] == 'Licencias Microsoft Azure'


def test_priority_is_based_on_procurement_description():
    high = classify_opportunity('Renovación de licencias Microsoft 365 en Azure')
    hardware = classify_opportunity('Compra de laptops y servidores')
    unrelated = classify_opportunity('Compra de uniformes y agua potable')
    assert high['it_priority'] == 'Alta'
    assert high['it_score'] > hardware['it_score'] > unrelated['it_score']
    assert hardware['is_it'] == 1
    assert unrelated['is_it'] == 0
    assert 'Microsoft / Microsoft 365 / Azure' in high['it_labels']
    assert json.dumps(high['it_labels'], ensure_ascii=False)


def test_new_filters_use_saved_classification_not_institution_name():
    assert classified_sql('noventiq') == ('is_it = 1', {})
    clause, params = classified_sql('microsoft')
    assert 'it_labels' in clause and 'Microsoft / Microsoft 365 / Azure' in params['tech_area']
    assert classified_sql('all') == ('', {})
