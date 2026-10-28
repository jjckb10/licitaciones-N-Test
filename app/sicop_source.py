"""Read the public SICOP 'Datos abiertos concursos' report safely.

The report is rendered as HTML with pagination; its table layout is not an
API contract. Accept alternate header layouts, but never turn a blocked,
malformed or repeated page into a successful completed synchronization.
"""
from dataclasses import dataclass
import hashlib
import json
import time
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


def canonical_procedure_number(value):
    """Extract one SICOP identifier, never save an entire HTML cell as its ID.

    Some SICOP cells contain an ID plus a label or extra display text. Their
    raw content can exceed the DB's VARCHAR(140) and must not be saved as the
    procedure number. Multiple distinct/duplicated IDs are ambiguous.
    """
    candidate = unicodedata.normalize('NFKC', str(value or '')).upper()
    matches = PROCEDURE.findall(candidate)
    return matches[0].upper() if len(matches) == 1 else ''
PAGE_META = re.compile(
    r'(\d[\d.,]*)\s+resultados\s*\[\s*(\d+)\s+de\s+(\d+)\s+p[aá]ginas?\s*\]',
    re.I,
)


def _cells(tr):
    """Visible direct cells; remove *decorative* image-only cells only.

    SICOP places sort icons in separate empty header cells. Those cannot be
    counted as business columns. Real blank TDs must remain in place.
    """
    cells = []
    for cell in tr.find_all(['th', 'td'], recursive=False):
        value = cell.get_text(' ', strip=True).replace('\xa0', ' ').strip()
        if cell.name == 'th' and not value:
            continue  # sort/icon-only column before the actual column title
        cells.append(value)
    return cells


def _procedure_index(values):
    for idx, candidate in enumerate(values):
        if PROCEDURE.search(candidate):
            return idx
    return None


def _column_headers(trs):
    """Find a meaningful header, regardless of image-only THs between names."""
    for tr in trs:
        values = [v for v in _cells(tr) if v]  # empty sorting columns
        keys = [normalize(v) for v in values]
        if (any(x in PROCEDURE_HEADERS for x in keys)
                and any(x in ('institucion', 'institucion_contratante', 'comprador',
                              'entidad_contratante') for x in keys)
                and any(x in ('descripcion', 'descripcion_del_procedimiento') for x in keys)):
            return values
    return None


def _valid_record(record):
    for key, raw in record.items():
        if normalize(key) in PROCEDURE_HEADERS:
            identifier = canonical_procedure_number(raw)
            if not identifier:
                return False
            # Canonicalize *before* creating the page fingerprint or SQL rows.
            record[key] = identifier
            return True
    return False


def _extract_table(table):
    """Map fields from semantic headings, never the decorative Image columns.

    The actual report uses alternating image-only sort THs and text THs. A
    header offset by those THs previously mapped procedure types as buyers.
    Distinct text header cells now line up with the data cells; when the HTML
    contains a leading icon TD, anchor on the procedure ID to calculate offset.
    """
    trs = table.find_all('tr')
    headings = _column_headers(trs)
    found = []
    for tr in trs:
        values = _cells(tr)
        proc_index = _procedure_index(values)
        if proc_index is None:
            continue
        if headings is not None:
            header_idx = next(i for i, h in enumerate(headings)
                              if normalize(h) in PROCEDURE_HEADERS)
            offset = proc_index - header_idx
            record = {name: values[i + offset] for i, name in enumerate(headings)
                      if name and 0 <= i + offset < len(values)}
            if _valid_record(record):
                found.append(record)
                continue
        # Fallback for split/unlabeled headings with standard 15 field order.
        # Never infer a real institution from a procedure-type column.
        start = proc_index - 2  # No., Descripción, Número procedimiento
        if start >= 0 and len(values) - start >= len(REPORT_HEADERS):
            record = dict(zip(REPORT_HEADERS, values[start:start + len(REPORT_HEADERS)]))
            if _valid_record(record):
                found.append(record)
        elif start >= 0 and len(values) - start == len(REPORT_HEADERS) - 1:
            # This shorter legacy variant omits "Tipo modalidad".
            shorter = [h for h in REPORT_HEADERS if h != 'Tipo modalidad']
            record = dict(zip(shorter, values[start:]))
            if _valid_record(record):
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
    # Hash the *entire business content*, not only tender identifiers.
    # Adjacent pages can legitimately contain the same procedure/partida/linea
    # while differing in description, amount, currency or other fields.
    # The visible "No." is a display index and must not affect the fingerprint.
    canonical_rows = [
        {normalize(k): str(v).strip() for k, v in row.items()
         if normalize(k) not in ('no', 'n', 'numero')}
        for row in rows
    ]
    payload = json.dumps(canonical_rows, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'))
    signature = hashlib.sha256(payload.encode('utf-8')).hexdigest()
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


def _cache_busted_url(url, attempt):
    """Retry the *same* page without reusing a shared intermediary cache.

    Extra parameter is only used after detecting an actual repetition. SICOP
    may disregard it; therefore a retry is never deemed successful unless the
    returned rows change and the reported page number agrees.
    """
    if not attempt:
        return url
    parts = urlsplit(url)
    params = parse_qsl(parts.query, keep_blank_values=True)
    params.append(('_sicop_retry', f'{time.time_ns()}-{attempt}'))
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(params), parts.fragment))


def get_page(session, url, number, previous_signature=None):
    """Retrieve a page; retry actual duplicates twice, never silently skip.

    The caller supplies the preceding page's content digest when available.
    A real HTML problem or a server that ignores page_no is reported plainly.
    """
    for attempt in range(3):
        if attempt:
            time.sleep(attempt)  # gentler retry; don't hammer SICOP
        target = _cache_busted_url(paged_url(url, number), attempt)
        try:
            kwargs = {'timeout': (8, 18)}
            if attempt:
                kwargs['headers'] = {'Cache-Control': 'no-cache, no-store',
                                     'Pragma': 'no-cache'}
            response = session.get(target, **kwargs)
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
        if not page.rows or previous_signature is None or page.signature != previous_signature:
            return page
        if attempt == 2:
            raise SICOPSourceError(
                f'La página {number} contiene exactamente los mismos datos '
                'que la página anterior, incluso tras 3 consultas. '
                f'SICOP indica {page.total_results if page.total_results is not None else "?"} '
                'resultados. Se guardó el avance anterior; comprobá la '
                'paginación desde un navegador o importá el CSV oficial. '
                'No se omiten páginas ni se inventan registros.')
    raise SICOPSourceError('No se pudo verificar la página de SICOP.')
