"""Conservative, explainable SOC opportunity map.

Combines the existing SICOP rule set with the external guide's useful hierarchy:
explicit SOC, equivalent operational service, related complementary purchases,
and non-SOC (including physical guarding). A suggestion is *not* an assertion
that the pliego requires a SOC. Input must be the procurement description, not
buyer/awardee names.
"""
import re
import unicodedata


def _clean(text):
    text = unicodedata.normalize('NFKD', str(text or '').lower())
    return ''.join(ch for ch in text if not unicodedata.combining(ch))


def _hits(text, phrases):
    return [p for p in phrases if re.search(r'(?<!\w)' + re.escape(p) + r'(?!\w)', text)]


DIRECT = (
    'soc', 'soc as a service', 'centro de operaciones de seguridad',
    'centro de operaciones de ciberseguridad', 'security operations center',
    'servicio soc', 'socaaS',
)
CONTEXT = (
    'ciberseguridad', 'seguridad informatica', 'seguridad de la informacion',
    'infraestructura tecnologica', 'infraestructura informatica',
    'sistemas informaticos', 'activos digitales', 'riesgos tecnologicos',
    'amenazas ciberneticas', 'infraestructura ti', 'eventos de seguridad',
    'redes informaticas',
)
MONITOR = ('monitoreo', 'monitorizacion', 'supervision continua',
           'vigilancia continua', 'deteccion de amenazas', 'alertas de seguridad',
           'analisis de eventos', 'correlacion de eventos', 'siem')
RESPONSE = ('respuesta a incidentes', 'gestion de incidentes', 'gestion y mitigacion',
            'mitigacion del riesgo', 'analisis de amenazas', 'incidentes de seguridad',
            'gestion de eventos', 'resolucion de incidentes', 'atencion de incidentes',
            'respuesta de eventos')
COMPLEMENTARY = ('firewall', 'edr', 'xdr', 'pentest', 'vulnerabilidades',
                 'seguridad perimetral', 'antivirus', 'mfa', 'iam', 'pam',
                 'seguridad en la nube', 'gestion de identidades',
                 'iso 27001', 'nist csf', 'hardening', 'waf',
                 'licenciamiento de ciberseguridad', 'proteccion de endpoints',
                 'noc', 'centro de operaciones de red')
PHYSICAL = ('guardas', 'guardias', 'vigilancia privada', 'seguridad fisica',
            'patrullaje', 'custodia de instalaciones', 'cctv', 'camara de seguridad',
            'camaras de seguridad', 'respuesta armada', 'videovigilancia')


def analyze_soc(description, object_classification=''):
    text = _clean(f'{description or ""} {object_classification or ""}')
    direct = _hits(text, DIRECT)
    context = _hits(text, CONTEXT)
    monitor = _hits(text, MONITOR)
    response = _hits(text, RESPONSE)
    complementary = _hits(text, COMPLEMENTARY)
    physical = _hits(text, PHYSICAL)

    # Safety: physical-only procurements should not be advertised as cyber SOC.
    if physical and not (context or direct):
        return {'tier': 'none', 'label': 'No SOC · seguridad física',
                'evidence': physical[:3], 'reason': 'Vigilancia física sin contexto de ciberseguridad.'}
    if direct:
        return {'tier': 'direct', 'label': 'SOC explícito',
                'evidence': direct[:3], 'reason': 'Se menciona directamente un centro de operaciones de seguridad.'}
    if context and monitor and response:
        evidence = list(dict.fromkeys(context[:1] + monitor[:2] + response[:2]))
        return {'tier': 'functional', 'label': 'SOC posible · funciones equivalentes',
                'evidence': evidence, 'reason': 'Combina contexto tecnológico, monitoreo y respuesta/mitigación.'}
    if context and (complementary or monitor or response):
        evidence = list(dict.fromkeys(context[:1] + complementary[:2] + monitor[:1] + response[:1]))
        return {'tier': 'complementary', 'label': 'Seguridad TI relacionada · revisar',
                'evidence': evidence, 'reason': 'Servicio complementario; no implica necesariamente un SOC.'}
    if complementary and not physical:
        return {'tier': 'complementary', 'label': 'Seguridad TI relacionada · revisar',
                'evidence': complementary[:3], 'reason': 'Tecnología de seguridad complementaria; revisar alcance.'}
    return {'tier': 'none', 'label': 'Sin evidencia SOC', 'evidence': [],
            'reason': 'La descripción no aporta evidencia suficiente.'}
