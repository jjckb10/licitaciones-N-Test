"""Technology-opportunity filters for SICOP procurement descriptions.

These rules represent search hints, NOT an official SICOP classification.
They deliberately inspect only the procurement object/description. The buyer's
name, awardee, and unrelated metadata must not trigger a technology match.

Keep this module free of database/Flask dependencies. ``technology_sql`` uses
bound parameters and SQL functions available in both SQLite and PostgreSQL.
"""

from collections import OrderedDict

# Distinctive phrases are preferred to broad words like "red", "TI", "servicio",
# "Google" in isolation where it would cause obvious false matches. All terms
# are compared case-insensitively; accented variants are included where needed.
TECH_AREAS = OrderedDict([
    ('microsoft', (
        'microsoft', 'azure', 'office 365', 'microsoft 365', 'm365',
        'sharepoint', 'exchange online', 'power bi', 'power platform',
        'dynamics 365', 'windows server', 'entra id', 'intune', 'copilot',
    )),
    ('google', (
        'google workspace', 'google cloud', 'google maps platform',
        'google', 'gcp', 'bigquery', 'chromebook',
    )),
    ('aws', (
        'amazon web services', 'amazon aws', 'aws', 'ec2', 'aws lambda',
    )),
    ('cloud', (
        'cloud', 'nube', 'azure', 'google cloud', 'amazon web services',
        'aws', 'gcp', 'iaas', 'paas', 'saas', 'hosting', 'hospedaje de servidores',
        'centro de datos', 'data center', 'datacenter',
    )),
    ('licensing', (
        'licencia', 'licencias', 'licenciamiento', 'suscripción de software',
        'suscripcion de software', 'suscripciones de software',
        'renovación de software', 'renovacion de software',
        'microsoft 365', 'office 365', 'm365', 'windows server', 'vmware',
        'adobe', 'software assurance',
    )),
    ('cybersecurity', (
        'ciberseguridad', 'seguridad informática', 'seguridad informatica',
        'seguridad de la información', 'seguridad de la informacion',
        'firewall', 'antivirus', 'antimalware', 'antiransomware',
        'pentest', 'penetration testing', 'vulnerabilidades',
        'soc ', 'centro de operaciones de seguridad', 'siem',
        'edr', 'xdr', 'zero trust', 'autenticación multifactor',
        'autenticacion multifactor', 'mfa', 'protección de endpoints',
        'proteccion de endpoints',
    )),
    ('hardware', (
        'hardware', 'computadora', 'computadoras', 'computador portátil',
        'computador portatil', 'computador personal', 'computador de escritorio',
        'equipo de cómputo', 'equipos de cómputo', 'equipos de computo',
        'laptop', 'notebook', 'estaciones de trabajo', 'workstation',
        'servidor', 'servidores', 'impresora', 'escáner', 'escaner',
        'almacenamiento nas', 'cabina de almacenamiento',
    )),
    ('networking', (
        'cableado estructurado', 'redes informáticas', 'redes informaticas',
        'red de datos', 'redes de datos', 'wifi', 'wi-fi', 'switch ',
        'switches', 'router', 'enrutador', 'conmutador', 'fortinet',
        'cisco', 'fibra óptica', 'fibra optica', 'virtualización',
        'virtualizacion', 'vmware', 'hiperconvergencia',
        'infraestructura tecnológica', 'infraestructura tecnologica',
    )),
    ('software', (
        'software', 'desarrollo de sistemas', 'desarrollo de aplicaciones',
        'desarrollo de plataforma', 'aplicación web', 'aplicacion web',
        'aplicación móvil', 'aplicacion movil', 'sistema informático',
        'sistema informatico', 'sistemas de información',
        'sistemas de informacion', 'plataforma digital',
        'solución tecnológica', 'solucion tecnologica',
        'base de datos', 'business intelligence', 'inteligencia de negocios',
        'automatización de procesos', 'automatizacion de procesos',
        'erp', 'crm',
    )),
    ('it_services', (
        'mesa de ayuda', 'help desk', 'service desk', 'soporte técnico',
        'soporte tecnico', 'soporte informático', 'soporte informatico',
        'servicios administrados', 'servicios gestionados',
        'mantenimiento informático', 'mantenimiento informatico',
        'mantenimiento de software', 'mantenimiento de equipos de cómputo',
        'mantenimiento de equipos de computo', 'transformación digital',
        'transformacion digital', 'migración de servidores',
        'migracion de servidores', 'implementación tecnológica',
        'implementacion tecnologica', 'consultoría en tecnología',
        'consultoria en tecnologia',
    )),
])

TECH_FILTER_OPTIONS = (
    ('all', 'Todas las licitaciones'),
    ('noventiq', 'Todas las oportunidades TI · perfil Noventiq'),
    ('microsoft', 'Microsoft / Microsoft 365 / Azure'),
    ('google', 'Google / Workspace / Google Cloud'),
    ('aws', 'Amazon Web Services (AWS)'),
    ('cloud', 'Cloud y servicios de nube'),
    ('licensing', 'Licencias y suscripciones de software'),
    ('cybersecurity', 'Ciberseguridad'),
    ('hardware', 'Hardware y equipos'),
    ('networking', 'Redes e infraestructura'),
    ('software', 'Software y desarrollo'),
    ('it_services', 'Servicios y soporte TI'),
)

AREA_LABELS = {key: label for key, label in TECH_FILTER_OPTIONS
               if key not in ('all', 'noventiq')}


def selected_filter(selected):
    return selected if selected in dict(TECH_FILTER_OPTIONS) else 'all'


def terms_for(selected):
    """All meaningful search words for a category, deduplicated in order."""
    selected = selected_filter(selected)
    if selected == 'all':
        return ()
    areas = TECH_AREAS.values() if selected == 'noventiq' else [TECH_AREAS[selected]]
    return tuple(dict.fromkeys(phrase.lower() for terms in areas for phrase in terms))


def technology_sql(selected):
    """Return a SQL fragment and its bind values (portable SQLite/Postgres).

    Only the procurement description and object classification are searched.
    Do not concatenate request parameters into SQL. The search is indicative:
    a procurement titled just 'servicios varios' may be missed.
    """
    terms = terms_for(selected)
    if not terms:
        return '', {}
    source = "(LOWER(COALESCE(description, '')) || ' ' || LOWER(COALESCE(object_classification, '')))"
    fragments = []
    params = {}
    for i, word in enumerate(terms):
        name = f'tech_{i}'
        fragments.append(f'{source} LIKE :{name}')
        params[name] = f'%{word}%'
    return '(' + ' OR '.join(fragments) + ')', params


def matched_areas(description, object_classification=''):
    """Informational category labels to accompany each matching result."""
    document = f'{description or ""} {object_classification or ""}'.lower()
    return [AREA_LABELS[key] for key, terms in TECH_AREAS.items()
            if any(phrase.lower() in document for phrase in terms)]

# Transparent, rules-based commercial prioritization; no automatic claim of
# eligibility, real-time opening, budget, or deadline is made.
AREA_WEIGHTS = {
    'microsoft': 32, 'google': 29, 'aws': 30, 'cloud': 22,
    'licensing': 27, 'cybersecurity': 25, 'hardware': 21,
    'networking': 17, 'software': 19, 'it_services': 17,
}


def classify_opportunity(description, object_classification=''):
    """Classify during ingestion as well as for backfills and CSV uploads.

    Returns a stable serializable dict. Words come only from the procurement
    object's description, never the institution/awardee, to reduce false hits.
    """
    labels = matched_areas(description, object_classification)
    raw = f'{description or ""} {object_classification or ""}'.lower()
    keys = [key for key, terms in TECH_AREAS.items()
            if any(word.lower() in raw for word in terms)]
    if not keys:
        return {'is_it': 0, 'it_score': 0, 'it_priority': 'No aplica',
                'it_labels': [], 'it_reasons': []}
    # Cap accumulating broad keyword matches; a high score requires coverage
    # of at least two commercial areas, or highly distinctive platform terms.
    weights = sorted((AREA_WEIGHTS[k] for k in keys), reverse=True)
    score = min(100, 18 + weights[0] + sum(weights[1:3]) // 2)
    priority = ('Alta' if score >= 62 else
                'Media' if score >= 37 else 'Baja')
    return {'is_it': 1, 'it_score': score, 'it_priority': priority,
            'it_labels': labels, 'it_reasons': keys}


def classified_sql(selected):
    """Use automatic classification persisted during sync, not ad hoc LIKEs.

    Like technology_sql, this returns only a safe SQL predicate and bindings.
    V3 still has technology_sql for backward compatibility with existing tests.
    """
    import json
    selected = selected_filter(selected)
    if selected == 'all':
        return '', {}
    if selected == 'noventiq':
        return 'is_it = 1', {}
    label = AREA_LABELS[selected]
    return 'COALESCE(it_labels, \'\') LIKE :tech_area', {
        'tech_area': '%' + json.dumps(label, ensure_ascii=False) + '%',
    }
