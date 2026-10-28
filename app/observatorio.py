"""Official Observatorio de Compra Pública monthly SICOP ZIP imports.

The official archive contains several CSVs; DetalleCarteles is the cartel
header, NOT a listing of currently open tenders. We do not infer open status.
No arbitrary URLs, filesystem extraction, or entire ZIP members loaded into RAM.
"""
import csv
from datetime import date
import io
import re
import tempfile
import zipfile
from urllib.parse import urlsplit

import requests

from app.publication import parse_publication_date
from app.sicop_source import normalize

BLOB_BASE = ('https://dlsaobservatorioprod.blob.core.windows.net/'
             'fs-synapse-observatorio-produccion/Zip/')
MAX_ZIP_BYTES = 75 * 1024 * 1024
MAX_MEMBER_BYTES = 200 * 1024 * 1024
MAX_ROWS = 700_000
PROCEDURE_RE = re.compile(r'^(?:19|20)\d{2}[A-Z]{2,4}-\d{4,8}-\d{8,12}$', re.I)


class ObservatoryImportError(ValueError):
    pass


def validate_month(month):
    if not re.fullmatch(r'20\d{4}', str(month or '')):
        raise ObservatoryImportError('Mes inválido; usá AAAAMM (ejemplo: 202610).')
    try:
        year, num = int(month[:4]), int(month[4:])
        if not 2010 <= year <= date.today().year or not 1 <= num <= 12:
            raise ValueError()
        if (year, num) > (date.today().year, date.today().month):
            raise ValueError()
    except ValueError as exc:
        raise ObservatoryImportError('Mes fuera del rango publicado.') from exc
    return month


def official_archive_url(month):
    return BLOB_BASE + validate_month(month) + '.zip'


def download_archive(month, session=None):
    """Download one trusted official month into a disk-backed temporary file."""
    url = official_archive_url(month)
    own = session is None
    session = session or requests.Session()
    tmp = tempfile.SpooledTemporaryFile(max_size=5 * 1024 * 1024)
    try:
        with session.get(url, stream=True, timeout=(8, 50), allow_redirects=False) as response:
            if response.status_code == 404:
                raise ObservatoryImportError(f'No se publicó el archivo {month}.zip.')
            response.raise_for_status()
            size = 0
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if chunk:
                    size += len(chunk)
                    if size > MAX_ZIP_BYTES:
                        raise ObservatoryImportError('Archivo ZIP mayor al límite de seguridad (75 MB).')
                    tmp.write(chunk)
        tmp.seek(0)
        if not zipfile.is_zipfile(tmp):
            raise ObservatoryImportError('El Observatorio no devolvió un ZIP válido.')
        return tmp
    except (requests.RequestException, zipfile.BadZipFile) as exc:
        raise ObservatoryImportError(f'No fue posible descargar el ZIP oficial: {exc}') from exc
    except Exception:
        tmp.close()
        raise
    finally:
        if own:
            session.close()


def _columns(row):
    return {normalize(k): (v or '').strip() for k, v in row.items() if k}


def _field(data, *names):
    return next((data[normalize(k)] for k in names if data.get(normalize(k))), '')


def _file_for(archive, stem):
    matches = [item for item in archive.infolist()
               if not item.is_dir() and item.filename.rsplit('/', 1)[-1].lower().startswith(stem.lower())
               and item.filename.lower().endswith('.csv')]
    return sorted(matches, key=lambda item: len(item.filename))[0] if matches else None


def _csv_rows(archive, member):
    if member.file_size > MAX_MEMBER_BYTES:
        raise ObservatoryImportError(f'{member.filename} es demasiado grande para importarlo en la web.')
    # Stream CSV, using utf-8-sig with replacement only for malformed bytes.
    # Some historical files are Latin-1; select encoding from a short sample.
    with archive.open(member) as binary:
        sample = binary.read(8192)
    try:
        sample.decode('utf-8-sig')
        encoding = 'utf-8-sig'
    except UnicodeDecodeError:
        encoding = 'latin-1'
    with archive.open(member) as binary:
        with io.TextIOWrapper(binary, encoding=encoding, errors='replace', newline='') as stream:
            try:
                dialect = csv.Sniffer().sniff(sample.decode(encoding, errors='replace'), delimiters=',;\t|')
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(stream, dialect=dialect)
            if not reader.fieldnames:
                raise ObservatoryImportError(f'{member.filename} no tiene encabezados CSV.')
            for row in reader:
                yield _columns(row)


def _buyers(archive):
    member = _file_for(archive, 'InstitucionesRegistradas')
    if member is None:
        return {}
    buyers = {}
    for idx, row in enumerate(_csv_rows(archive, member)):
        if idx > MAX_ROWS:
            break
        code = _field(row, 'CEDULA_INSTITUCION', 'CEDULA_JURIDICA', 'NRO_CEDULA', 'CEDULA')
        name = _field(row, 'NOMBRE_INSTITUCION', 'NOM_INSTITUCION', 'NOMBRE_ENTIDAD',
                      'NOMBRE', 'RAZON_SOCIAL', 'NOMBRE_INSTITUCION_CONTRATANTE')
        if code and name:
            buyers[re.sub(r'\D', '', code)] = name
    return buyers


def iter_carteles(file_obj):
    """Yield normalized cartel header rows. No ZIP extraction to filesystem.

    A source field can be absent; unknown buyers and close dates are left
    blank. We never substitute purchase-unit names for the buyer's name.
    """
    try:
        with zipfile.ZipFile(file_obj) as archive:
            member = _file_for(archive, 'DetalleCarteles')
            if member is None:
                raise ObservatoryImportError('El ZIP no contiene DetalleCarteles.csv.')
            buyers = _buyers(archive)
            found = 0
            for i, row in enumerate(_csv_rows(archive, member)):
                if i >= MAX_ROWS:
                    raise ObservatoryImportError('Se alcanzó el máximo de filas por archivo.')
                procedure = _field(row, 'NRO_PROCEDIMIENTO', 'NUMERO_PROCEDIMIENTO',
                                   'INST_CARTEL_NO', 'NUMERO_DE_PROCEDIMIENTO').strip().upper()
                if not PROCEDURE_RE.fullmatch(procedure):
                    continue
                descr = _field(row, 'DESCRIPCION', 'DESCRIPCION_PROCEDIMIENTO', 'OBJETO_CONTRATACION')
                code = re.sub(r'\D', '', _field(row, 'CEDULA_INSTITUCION'))
                buyer = _field(row, 'NOMBRE_INSTITUCION', 'INSTITUCION',
                               'INSTITUCION_CONTRATANTE') or buyers.get(code, '')
                found += 1
                yield {
                    'procedure_number': procedure,
                    'description': descr,
                    'institution': buyer,
                    'procedure_type': _field(row, 'TIPO_PROCEDIMIENTO'),
                    'modality': _field(row, 'MODALIDAD_PROCEDIMIENTO'),
                    'object_classification': _field(row, 'CLASIFICACION_OBJETO'),
                    'publication_date': parse_publication_date(_field(row, 'FECHA_PUBLICACION')),
                    'closing_date': parse_publication_date(_field(row, 'FECHA_CIERRE_RECEPCION',
                                                                 'FECHA_APERTURA_OFERTAS',
                                                                 'FECHA_APERTURA', 'FECHA_CIERRE')),
                    'status': 'Vigencia por verificar en SICOP',
                    'currency': _field(row, 'MONEDA'),
                    'amount': _field(row, 'MONTO_ESTIMADO', 'PRESUPUESTO', 'MONTO'),
                }
            if not found:
                raise ObservatoryImportError('No se hallaron procedimientos válidos en DetalleCarteles.')
    except (zipfile.BadZipFile, OSError, EOFError) as exc:
        raise ObservatoryImportError('ZIP oficial dañado o incompleto.') from exc
