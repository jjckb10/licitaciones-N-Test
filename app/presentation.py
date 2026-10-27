"""SICOP public-report fields, safe display strings, and commercial summaries."""
import re
import unicodedata

# These are procurement *types*, not buyer identities or contract summaries.
TYPE_NAMES = {
    'licitacion reducida', 'licitacion menor', 'licitacion mayor',
    'procedimientos especiales', 'procedimiento especial',
    'contratacion directa', 'concurso publico', 'bien', 'bienes',
    'servicios', 'bienes servicios', 'cantidad definida', 'segun demanda',
}


def _normalized(text):
    text = unicodedata.normalize('NFKD', str(text or ''))
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r'[^a-z0-9]+', ' ', text.casefold()).strip()


def is_procedure_label(text):
    return _normalized(text) in TYPE_NAMES


def buyer_name(text):
    """Never report 'LICITACIÓN REDUCIDA' as the purchasing institution."""
    value = re.sub(r'\s+', ' ', str(text or '')).strip()
    return '' if is_procedure_label(value) or _normalized(value) in (
        '', 'nan', 'none', 'null', 'no disponible', 'no informado') else value


def clean_description(text):
    value = re.sub(r'\s+', ' ', str(text or '')).strip()
    return '' if is_procedure_label(value) or _normalized(value) in (
        '', 'nan', 'none', 'null') else value


def brief_description(text, limit=165):
    value = clean_description(text)
    if not value:
        return 'Descripción no disponible en la fuente'
    if len(value) <= limit:
        return value
    snippet = value[:limit - 1].rsplit(' ', 1)[0]
    return (snippet if len(snippet) >= 60 else value[:limit - 1]).rstrip(' ,;') + '…'
