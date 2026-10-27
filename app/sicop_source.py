"""Extract records from SICOP's *paginated* public open-data table.

The page currently defaults to 10 rows, and accepts page_no in its URL.
Never treat a network error, invalid HTML or a repeated first page as the end.
"""
from dataclasses import dataclass
import hashlib
import math
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


def parse_page(html):
    soup = BeautifulSoup(html, 'lxml')
    page_text = soup.get_text(' ', strip=True)
    if ('no fue posible acceder' in normalize(page_text).replace('_', ' ')
            or 'id de soporte' in normalize(page_text).replace('_', ' ')):
        raise SICOPSourceError('SICOP devolvió una página de error en lugar de resultados.')

    # SICOP displays: 3867 resultados [ 4 de 387 páginas ]
    total_match = re.search(r'([\d.,\s]+)\s+resultados\s*\[\s*(\d+)\s+de\s+(\d+)\s+p[aá]ginas?\s*\]',
                            page_text, re.IGNORECASE)
    pages = page_no = total = None
    if total_match:
        total = int(re.sub(r'\D', '', total_match.group(1)))
        page_no = int(total_match.group(2))
        pages = int(total_match.group(3))
    elif re.search(r'\b0\s+resultados\b', page_text, re.IGNORECASE):
        total, pages = 0, 0

    headers = records = None
    for table in soup.find_all('table'):
        trs = table.find_all('tr')
        for idx, tr in enumerate(trs[:4]):
            cells = tr.find_all(['th', 'td'], recursive=False)
            candidate = [cell.get_text(' ', strip=True) for cell in cells]
            keys = [normalize(col) for col in candidate]
            if not any(key in ('numero_procedimiento', 'numero_de_procedimiento', 'procedure_number') for key in keys):
                continue
            headers = candidate
            records = []
            for row in trs[idx+1:]:
                data = row.find_all('td', recursive=False)
                if len(data) != len(headers):
                    continue
                vals = [cell.get_text(' ', strip=True) for cell in data]
                r = dict(zip(headers, vals))
                if any(r.get(key, '') for key in ('Número procedimiento', 'Numero procedimiento', 'procedure_number')):
                    records.append(r)
                else:
                    # Alternate display titles are also accepted.
                    for key, value in zip(keys, vals):
                        if key in ('numero_procedimiento', 'numero_de_procedimiento', 'procedure_number') and value:
                            records.append(r)
                            break
            break
        if headers is not None:
            break
    if records is None:
        if total == 0:
            records = []
        else:
            raise SICOPSourceError('No se encontró una tabla válida de procedimientos en SICOP.')
    if total is not None and total > 0 and not records:
        raise SICOPSourceError('SICOP informa resultados pero devolvió una tabla vacía.')

    ids = []
    for r in records:
        nr = {normalize(k): str(v) for k,v in r.items()}
        ids.append('|'.join((nr.get('numero_procedimiento') or nr.get('numero_de_procedimiento') or nr.get('procedure_number') or '',
                             nr.get('partida', ''), nr.get('numero_de_linea') or nr.get('numero_linea', ''),
                             nr.get('adjudicatario', ''), nr.get('estado_de_procedimiento', ''))))
    signature = hashlib.sha256('\n'.join(ids).encode('utf-8')).hexdigest()
    return SourcePage(records, page_no, pages, total, signature)


def paged_url(url, page_number):
    split = urlsplit(url)
    if split.scheme not in ('http','https'):
        raise SICOPSourceError('La URL de SICOP debe utilizar http o https.')
    params = [(k,v) for k,v in parse_qsl(split.query, keep_blank_values=True) if k!='page_no']
    params.append(('page_no', str(page_number)))
    return urlunsplit((split.scheme,split.netloc,split.path,urlencode(params),split.fragment))


def make_session():
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; SICOP-Opportunity-Hub/3.0)',
                      'Accept': 'text/html,application/xhtml+xml'})
    retry = Retry(total=2, connect=2, read=2, backoff_factor=0.5,
                  status_forcelist=[429, 500, 502, 503, 504], allowed_methods=['GET'])
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://', HTTPAdapter(max_retries=retry))
    return s


def get_page(session, url, number):
    try:
        response = session.get(paged_url(url, number), timeout=(8, 18))
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SICOPSourceError(f'Error de conexión con SICOP en página {number}: {exc}') from exc
    return parse_page(response.text)
