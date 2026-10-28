# SICOP Opportunity Hub V9.2 — corrección de Moneda y campos antiguos

## Problema reproducido

En Render, el recorrido se detuvo en la **página 47 de 544** con:

- `StringDataRightTruncation: value too long for type character varying(140)`.
- `Campo Moneda demasiado largo en 2023LY-000021-0020600001`.

Son dos problemas distintos que pueden coexistir:

1. Una fila del HTML de «Datos abiertos concursos» puede traer menos celdas de datos que encabezados (por ejemplo, omitir el dato «Tipo modalidad»), provocando un **desplazamiento de las columnas**.
2. En una base de PostgreSQL creada con versiones antiguas, `CREATE TABLE IF NOT EXISTS` **no convierte** sus columnas `VARCHAR(140)` a `TEXT`, aunque el código más reciente ya las declare como `TEXT`.

## Cambios

1. El extractor busca la variante de tabla coherente y verifica el campo **Moneda**. No inventa montos, adjudicatarios ni nombres de instituciones cuando la estructura no puede validarse.
2. `upsert_rows()` convierte monedas reconocidas (`CRC`, `USD`, `EUR`, «Colones», «Dólares», etc.) a un código consistente. Una frase larga o un valor numérico en Moneda **no se inserta** como si fuera moneda válida.
3. Cuando una fila sigue siendo inconsistente, la sincronización guarda el registro original (incluidas las celdas y encabezados cuando están disponibles) en `sicop_row_issues`. Las demás filas válidas se importan normalmente. La tabla de incidencias y el cursor de sincronización se actualizan dentro de la **misma transacción**. La fila queda pendiente de revisión y **no figura todavía como una licitación válida**.
4. Un aviso administrativo muestra el número de incidencias y permite descargar el CSV `sicop-incidencias.csv` con las filas pendientes. El historial de incidentes permanece en PostgreSQL.
5. La migración automática de PostgreSQL amplía **solo columnas de texto** anteriores (`institution`, `description`, `status`, `awardee` y otros campos descriptivos) de `VARCHAR(n)` a `TEXT` cuando haga falta. No cambia claves, números de procedimiento, importes ni usuarios.
6. No se eliminan datos ni se reinicia el recorrido. El resto de V9.1 (SOC, Observatorio, Noventiq, usuarios) sigue igual.

## Instalación en GitHub y Render

1. Usar el ZIP **actualización** y subir los archivos conservando sus rutas; es imprescindible agregar `app/record_quality.py` además de reemplazar `app/main.py` y `app/sicop_source.py`.
2. Reemplazar también `app/templates/index.html`. Los archivos de `tests/` son recomendados para validar el cambio.
3. Hacer commit/merge a `main` y desplegar esa revisión en el **mismo Web Service de Render**.
4. **Conservar `DATABASE_URL` apuntando a la misma instancia PostgreSQL**. No crear otra base, no eliminar tablas y no ejecutar SQL manual.
5. Abrir `/health` y comprobar que la aplicación responde y que puede acceder a PostgreSQL. La primera petición inicializa/migra las columnas compatibles.
6. Entrar como administrador. La sincronización debe seguir indicando **«Continuar desde página 47»** si nada externo ha reiniciado el estado. Elegir **«Reanudar sincronización»** o **«Solo siguiente bloque»**.
7. Si se guardaron incidencias, descargarlas desde «Administración · Sincronización SICOP» para revisar su contenido. No asumir que todas las 544 páginas son impecables ni que cada fila problemática fue recuperada automáticamente.

## Verificación

- `python -m py_compile app/*.py`
- `python -m pytest -q` → **84 pruebas aprobadas, 2 omitidas** en el entorno local (Flask no disponible en este entorno de pruebas).
- Pruebas adicionales: página donde falta la modalidad; página con modalidad; normalización de moneda; lote con una fila inconsistente y otras válidas; almacenamiento de la incidencia sin duplicarla; migración de campos de texto antiguos.

**Pendiente:** prueba del HTML real de la página 47 y ejecución en el servicio de Render del usuario (el portal SICOP puede bloquear solicitudes externas). No se ha cambiado ni consultado su base PostgreSQL en vivo.

## Archivos clave

- `app/record_quality.py` — validación de códigos de moneda.
- `app/sicop_source.py` — alineación de celdas y preservación de origen sospechoso.
- `app/main.py` — migración conservadora, cuarentena transaccional y exportación de incidencias.
- `app/templates/index.html` — aviso y descarga para administradores.
- `tests/test_v9_2_source_quality.py` — pruebas de regresión.
