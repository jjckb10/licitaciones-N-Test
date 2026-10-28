"""One row per Observatory cartel in the unified dashboard; legacy rows preserved.

Only the read model is deduplicated across sources. Underlying rows/lines are
never deleted, so imports are reversible and the older HTML source survives.
"""

CATALOG_FROM = '''(
 SELECT o.procedure_number || '|0|0' AS id,
        COALESCE(NULLIF(o.description,''),NULLIF(t.description,''),'') AS description,
        o.procedure_number,
        COALESCE(NULLIF(o.institution,''),NULLIF(t.institution,''),'') AS institution,
        COALESCE(NULLIF(o.procedure_type,''),NULLIF(t.procedure_type,''),'') AS procedure_type,
        COALESCE(NULLIF(o.modality,''),NULLIF(t.modality,''),'') AS modality,
        COALESCE(NULLIF(o.object_classification,''),NULLIF(t.object_classification,''),'') AS object_classification,
        '0' AS item, '0' AS line_number,
        COALESCE(t.awardee,'') AS awardee,
        COALESCE(NULLIF(o.currency,''),t.currency) AS currency,
        COALESCE(o.amount,t.amount) AS amount,
        t.exchange_rate,t.awarded_crc,
        o.status AS status,o.source_url,
        o.first_seen,o.last_seen,
        COALESCE(o.publication_date,t.publication_date) AS publication_date,
        o.is_it,o.it_score,o.it_priority,o.it_labels,o.classification_version,
        'Observatorio (vigencia sin verificar)' AS source_kind,
        o.closing_date
 FROM observatory_tenders o
 LEFT JOIN (SELECT t.*,ROW_NUMBER() OVER
   (PARTITION BY procedure_number ORDER BY last_seen DESC,id ASC) AS rn
   FROM tenders t) t ON t.procedure_number=o.procedure_number AND t.rn=1
 UNION ALL
 SELECT t.id,t.description,t.procedure_number,t.institution,t.procedure_type,
        t.modality,t.object_classification,t.item,t.line_number,t.awardee,
        t.currency,t.amount,t.exchange_rate,t.awarded_crc,t.status,t.source_url,
        t.first_seen,t.last_seen,t.publication_date,t.is_it,t.it_score,
        t.it_priority,t.it_labels,t.classification_version,
        'Reporte SICOP / CSV' AS source_kind,NULL AS closing_date
 FROM tenders t
 WHERE NOT EXISTS (SELECT 1 FROM observatory_tenders o
                   WHERE o.procedure_number=t.procedure_number)
) AS tenders'''
