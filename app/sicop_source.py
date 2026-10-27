"""Read the public SICOP 'Datos abiertos concursos' report safely.

The report is rendered as HTML with pagination; its table layout is not an
API contract. Accept alternate header layouts, but never turn a blocked,
malformed or repeated page into a successful completed synchronization.
"""
from dataclasses import dataclass
import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class SICOPSourceError(RuntimeError):
    pass


def normalize(value):
    value = unicodedata.normalize('NFKD', str(value or ''))
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


@dataclass
class SourcePage:
    rows: list
    page_number: int | None
    total_pages: int | None
    total_results: int | None
    signature: str


# SICOP's displayed columns (including No., which isn't persisted).
REPORT_HEADERS = (
    'No.', 'Descripción', 'Número procedimiento', 'Institución',
    'Tipo Procedimiento', 'Tipo modalidad', 'Clasificación objeto',
    'Partida', 'Número de línea', 'Adjudicatario', 'Moneda', 'Monto',
    'Tipo de cambio', 'Monto adjudicado en colones',
    'Estado de procedimiento',
)
PROCEDURE_HEADERS = {'numero_procedimiento', 'numero_de_procedimiento',
                     'procedure_number'}
# Proc numbers like 2026LD-000123-0006400001, 2025XE-000853-0000400001.
PROCEDURE = re.compile(r'\b(?:19|20)\d{2}[A-Z]{2,4}-\d{4,8}-\d{8,12}\b', re.I)
PAGE_META = re.compile(
    r'(\d[\d.,]*)\s+resultados\s*\[\s*(\d+)\s+de\s+(\d+)\s+p[aá]ginas?\s*\]',
    re.I,
)


def _cells(tr):
    """Include both TD and TH: some report templates use either for rows."""
    return [c.get_text(' ', strip=True).replace('\xa0', ' ').strip()
            for c in tr.find_all(['th', 'td'], recursive=False)]


def _procedure_index(values):
    for idx, candidate in enumerate(values):
        if PROCEDURE.search(candidate):
            return idx
    return None


def _extract_table(table):
    """Align columns on the actual procedure number, not fragile cell counts.

    SICOP sometimes adds an unnamed icon/action column to the header or to
    data rows. Anchoring on a procedure ID avoids shifting all columns when
    these decorative cells appear. The canonical 15-column report order is a
    fallback when the visible header has been split into multiple rows.
    """
    trs = table.find_all('tr')
    header_cells = None
    header_procedure_index = None
    for tr in trs:
        cells = _cells(tr)
        keys = [normalize(v) for v in cells]
        try:
            idx = next(i for i, k in enumerate(keys) if k in PROCEDURE_HEADERS)
        except StopIteration:
            continue
        # Don't identify a floating help/search table as the report table.
        if len(cells) >= 8:
            header_cells, header_procedure_index = cells, idx
            break

    found = []
    for tr in trs:
        vals = _cells(tr)
        if len(vals) < 10:
            continue
        proc_idx = _procedure_index(vals)
        if proc_idx is None:
            continue
        # Prefer the named header, regardless of the number of decorative TDs.
        if header_cells is not None:
            offset = proc_idx - header_procedure_index
            record = {name: vals[i + offset] for i, name in enumerate(header_cells)
                      if normalize(name) not in ('', 'image')
                      and 0 <= i + offset < len(vals)}
            if not any(PROCEDURE.search(record.get(k, '')) for k in header_cells
                       if normalize(k) in PROCEDURE_HEADERS):
                continue
            # Header includes controls but data doesn't (or vice versa):
            # use the fixed report column order if critical names are missing.
            if any(normalize(k) == 'institucion' for k in record) and any(
                    normalize(k) == 'descripcion' for k in record):
                found.append(record)
                continue
        # In some SICOP variants headings are nested/split into another row.
        # Public report columns are fixed relative to the procedure number.
        start = proc_idx - 2  # No., Descripción, Número procedimiento
        if start >= 0 and len(vals) - start >= len(REPORT_HEADERS) - 1:
            record = dict(zip(REPORT_HEADERS, vals[start:start + len(REPORT_HEADERS)]))
            if PROCEDURE.search(record['Número procedimiento']):
                found.append(record)
    return found


def parse_page(html):
    soup = BeautifulSoup(html, 'lxml')
    page_text = soup.get_text(' ', strip=True)
    normalized_text = normalize(page_text).replace('_', ' ')
    if ('no fue posible acceder' in normalized_text or
            'id de soporte' in normalized_text or
            'access denied' in page_text.lower()):
        raise SICOPSourceError(
            'SICOP devolvió una página de bloqueo/error (ID de soporte). '
            'No se importaron datos; revisá el acceso desde Render.')

    match = PAGE_META.search(page_text)
    page_no = pages = total = None
    if match:
        total = int(re.sub(r'\D', '', match.group(1)))
        page_no = int(match.group(2))
        pages = int(match.group(3))
    elif re.search(r'\b0\s+resultados\b', page_text, re.I):
        total = pages = 0

    # A page may contain several layout tables; choose the actual result table.
    rows = max((_extract_table(t) for t in soup.find_all('table')),
               key=len, default=[])
    if total is not None and total > 0 and not rows:
        raise SICOPSourceError(
            f'SICOP indicó {total:,} resultados, pero no se pudieron leer '
            'las filas de su HTML. Puede haber cambiado la tabla o la '
            'respuesta estar incompleta; no se avanzó el cursor.')
    if total is None and not rows:
        raise SICOPSourceError(
            'SICOP no entregó una tabla válida de procedimientos ni '
            'un total de resultados verificable.')
    ids = []
    for row in rows:
        normalized = {normalize(k): str(v) for k, v in row.items()}
        ids.append('|'.join((normalized.get('numero_procedimiento') or
                             normalized.get('numero_de_procedimiento') or '',
                             normalized.get('partida', ''),
                             normalized.get('numero_de_linea', ''),
                             normalized.get('adjudicatario', ''),
                             normalized.get('estado_de_procedimiento', ''))))
    signature = hashlib.sha256('\n'.join(ids).encode('utf-8')).hexdigest()
    return SourcePage(rows, page_no, pages, total, signature)


def paged_url(url, page_number):
    split = urlsplit(url)
    if split.scheme not in ('http', 'https'):
        raise SICOPSourceError('La URL de SICOP debe utilizar http o https.')
    params = [(k, v) for k, v in parse_qsl(split.query, keep_blank_values=True)
              if k != 'page_no']
    params.append(('page_no', str(page_number)))
    return urlunsplit((split.scheme, split.netloc, split.path,
                       urlencode(params), split.fragment))


def make_session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (compatible; SICOP-Opportunity-Hub/3.1)',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'es-CR,es;q=0.9',
    })
    retry = Retry(total=2, connect=2, read=2, backoff_factor=0.5,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=['GET'])
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://', HTTPAdapter(max_retries=retry))
    return s


def get_page(session, url, number):
    try:
        response = session.get(paged_url(url, number), timeout=(8, 18))
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else '?'
        raise SICOPSourceError(
            f'SICOP respondió HTTP {status} en la página {number}. '
            'Si bloquea a Render, debe resolverse con SICOP o utilizar '
            'el CSV oficial; la paginación no elimina el bloqueo.') from exc
    except requests.RequestException as exc:
        raise SICOPSourceError(
            f'Error de conexión con SICOP en página {number}: {exc}') from exc
    page = parse_page(response.text)
    if page.page_number is not None and page.page_number != number:
        raise SICOPSourceError(
            f'SICOP respondió página {page.page_number} cuando se pidió '
            f'la {number}. No se avanzó para evitar datos repetidos.')
    return page
