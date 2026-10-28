# SICOP Opportunity Hub V9.1 — corrección de sincronización (PostgreSQL VARCHAR(140))

## Problema resuelto

En algunos resultados del reporte HTML de SICOP el valor de la celda **Número procedimiento** aparece con una etiqueta u otro texto añadido. El parser anterior consideraba válida toda la celda si contenía una coincidencia con un número SICOP, pero luego `upsert_rows` guardaba **la celda completa** en `tenders.procedure_number VARCHAR(140)`. PostgreSQL interrumpía la importación con `psycopg.errors.StringDataRightTruncation`.

## Cambios

- `app/sicop_source.py`: nueva función `canonical_procedure_number`, que identifica el **único número de procedimiento oficial** en la celda; rechaza identificadores ausentes o ambiguos. `_valid_record` normaliza ese campo antes de construir el contenido de la página y su huella de verificación.
- `app/main.py`: `upsert_rows` normaliza el número también al cargar CSV y se niega a guardar datos corruptos. Ante errores de columnas (moneda, estado, partida o línea) entrega un mensaje específico. No recorta arbitrariamente un número de procedimiento, no cambia las claves existentes y no elimina registros.
- `tests/test_v9_procedimiento_longitud.py`: regresiones para valores de más de 140 caracteres, CSV, filas HTML completas y actualizaciones sin duplicados.

## Cómo instalar en la versión V9 actual

1. Si ya existe información importante, descargá un respaldo de PostgreSQL.
2. En GitHub, **reemplazá** `app/sicop_source.py` y `app/main.py`; agregá `tests/test_v9_procedimiento_longitud.py`. Conservá los demás archivos de V9.
3. Hacé commit/merge en la rama `main`, y verificá que Render despliegue la nueva versión.
4. Conservá **exactamente la misma** `DATABASE_URL` y `SECRET_KEY` en Render. No reiniciés ni borres la base de datos.
5. Usá **Reanudar sincronización**. Los lotes ya confirmados se conservan; la página donde falló se intentará nuevamente. **No** pulses «Reiniciar recorrido» si solo querés continuar.
6. Comprobá el último estado de sincronización y los registros. Si vuelve a fallar, el nuevo error indicará si otra columna se desalineó.

**No es necesario ejecutar `ALTER TABLE`, recrear tablas ni aumentar `VARCHAR(140)`.** Si los datos están mal alineados, ampliar la columna ocultaría el problema y guardaría información incorrecta.

## Pruebas

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

Se ejecutaron **79 pruebas aprobadas; 2 omitidas** (las que necesitan Flask no estaban disponibles en el entorno de validación). Se probó el comportamiento con SQLite simulando los límites de esquema, pero no se ha validado una sincronización real en el PostgreSQL de Render ni la respuesta en vivo de SICOP.

Si Render muestra un error distinto (por ejemplo en `observatory_tenders` o en una columna de longitud diferente), revisá ese error por separado; esta actualización aborda la importación de `tenders.procedure_number`.
