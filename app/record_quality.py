"""Conservative validation for structured SICOP procurement columns.

Unknown values are not guessed or truncated: the importer can quarantine the
source record for review. No network, Flask or database dependency.
"""
import re
import unicodedata


def currency_code(raw):
    """Return an ISO-style currency code, '' for known blanks, or None if invalid.

    A number, a buyer name, or a long phrase in the currency column usually
    means the HTML row was misaligned. Never silently save it as a currency.
    """
    value = unicodedata.normalize('NFKC', str(raw or '')).strip()
    if not value:
        return ''
    folded = ''.join(c for c in unicodedata.normalize('NFKD', value)
                     if not unicodedata.combining(c)).upper()
    folded = re.sub(r'\s+', ' ', folded).strip()
    aliases = {
        '₡': 'CRC', '¢': 'CRC', 'COLONES': 'CRC', 'COLON': 'CRC',
        'COLONES COSTARRICENSES': 'CRC', 'COLON COSTARRICENSE': 'CRC',
        '$': 'USD', 'US$': 'USD', 'DOLARES': 'USD', 'DOLAR': 'USD',
        'DOLARES ESTADOUNIDENSES': 'USD',
        '€': 'EUR', 'EUROS': 'EUR', 'EURO': 'EUR',
        'NO APLICA': '', 'NO DISPONIBLE': '', 'NO INFORMADO': '', 'N/A': '',
    }
    if folded in aliases:
        return aliases[folded]
    # A random three-letter business term (e.g. SOC or IVA) is not a
    # currency. Accept common official ISO 4217 codes instead.
    supported_iso = {
        'CRC','USD','EUR','GBP','CAD','MXN','JPY','CHF','CNY','BRL',
        'COP','PEN','ARS','CLP','PYG','UYU','BOB','VES','GTQ','HNL',
        'NIO','PAB','DOP','BZD','JMD','TTD','BSD','BBD','HTG',
        'AUD','NZD','HKD','SGD','INR','KRW','TWD','IDR','MYR',
        'THB','PHP','VND','ILS','AED','SAR','QAR','KWD','TRY',
        'ZAR','EGP','MAD','RUB','UAH','PLN','CZK','HUF','RON',
        'DKK','NOK','SEK','ISK','BGN','HRK',
    }
    if folded in supported_iso:
        return folded
    return None
