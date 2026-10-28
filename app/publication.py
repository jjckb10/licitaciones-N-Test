"""Publication date from an explicit SICOP/CSV field, never import timestamps.

The public ``Datos abiertos concursos`` HTML report normally lacks this field.
Unknown dates must remain NULL, not silently replaced by when the scraper ran.
"""
from datetime import date, datetime
import re
import unicodedata

DATE_ALIASES = (
    'Fecha de publicación', 'Fecha publicación', 'Fecha/hora de publicación',
    'Fecha y hora de publicación', 'Fecha/hora publicación',
    'Fecha de publicación del procedimiento', 'Fecha de publicación del concurso',
    'Fecha de publicación del cartel', 'Fecha publicado', 'Fecha publicación concurso',
    'FECHAH_PUBLICACION', 'FECHA_PUBLICACION', 'PUBLICATION_DATE',
    'PUBLISHED_AT', 'publishedAt', 'publicationDate', 'publication_date',
    'published_at', 'fecha_publicacion', 'fecha_hora_publicacion',
)


def _normalize(value):
    value = unicodedata.normalize('NFKD', str(value or ''))
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def date_from_row(row):
    """Return ISO yyyy-mm-dd only if explicitly supplied by the source."""
    mapped = {_normalize(k): value for k, value in row.items()}
    for alias in DATE_ALIASES:
        value = mapped.get(_normalize(alias))
        if value not in (None, ''):
            parsed = parse_publication_date(value)
            if parsed:
                return parsed
    return None


def parse_publication_date(raw):
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    value = str(raw or '').strip().strip('"')
    if not value or value.lower() in ('nan', 'null', 'none', 'n/d', 'no disponible'):
        return None
    # Accept ISO 8601 with time, and Costa Rican dd/mm/yyyy dates with time.
    iso_match = re.match(r'^(\d{4}-\d{2}-\d{2})(?:[T\s].*)?$', value)
    if iso_match:
        try:
            result = date.fromisoformat(iso_match.group(1))
            return result.isoformat() if 2000 <= result.year <= 2100 else None
        except ValueError:
            return None
    for pattern, fmt in (
        (r'^(\d{2}/\d{2}/\d{4})(?:\s.*)?$', '%d/%m/%Y'),
        (r'^(\d{2}-\d{2}-\d{4})(?:\s.*)?$', '%d-%m-%Y'),
        (r'^(\d{4}/\d{2}/\d{2})(?:\s.*)?$', '%Y/%m/%d'),
        (r'^(\d{8})$', '%Y%m%d'),
    ):
        match = re.match(pattern, value)
        if match:
            try:
                result = datetime.strptime(match.group(1), fmt).date()
                return result.isoformat() if 2000 <= result.year <= 2100 else None
            except ValueError:
                return None
    return None


def display_publication_date(raw):
    parsed = parse_publication_date(raw)
    return f'{parsed[8:10]}/{parsed[5:7]}/{parsed[:4]}' if parsed else 'No disponible'
