# SICOP Opportunity Hub V3

Panel interno para consultar la tabla pública de **Datos abiertos concursos** de SICOP, conservarla en PostgreSQL, importarla desde archivos CSV y exportarla sin un límite artificial de registros.

## Qué se corrigió

- El código original hacía una única petición HTTP y, por eso, importaba normalmente 10 filas (la primera página de SICOP).
- Ahora usa `page_no` para pasar por **todas las páginas que el reporte publique** y deduplica las filas al guardar en PostgreSQL.
- Como Render puede cortar peticiones largas, guarda el avance en la tabla `sync_state`. Si quedan páginas, continúa en la siguiente ejecución de `/tasks/refresh` o mediante el botón del panel.
- El panel muestra resultados paginados de 25, 50 o 100 por página, sin `LIMIT 1000` como límite del conjunto de resultados.
- `/api/tenders` ofrece paginación y totales; `/export.csv` permite exportar **todas las filas almacenadas** que coinciden con los filtros.
- La importación CSV ahora procesa lotes de 500 filas para usar menos memoria.
- Detecta páginas de error y una eventual repetición de la misma página (por ejemplo, si SICOP ignora `page_no`), evitando declarar una sincronización falsa como completada.

## Alcance de la fuente

El reporte público usado por la aplicación es:

`https://www.sicop.go.cr/moduloBid/report/RE_DatosAbiertosConcursosView.jsp`

**Importante:** los «Datos abiertos concursos» incluyen campos de adjudicaciones y pueden tener filtros de fechas propios. **No son necesariamente todas las licitaciones con plazo de oferta vigente, ni todo el histórico de SICOP**. La aplicación extrae las páginas disponibles del reporte tal como las publica SICOP; no puede eludir restricciones, respuestas de error ni incorporar campos que la fuente no entrega. Para ampliar cobertura histórica, importá exportaciones oficiales CSV o revisá los [descargables masivos del Observatorio de Compra Pública](https://www.observatoriocomprapublica.go.cr/descargas-sicop/), cuyos esquemas pueden diferir y requerir adaptación.

La clave de fila se mantiene en la versión anterior: `número de procedimiento | partida | número de línea`. Si una misma combinación representa varias adjudicaciones distintas, el registro puede verse consolidado con el último dato recibido; antes de usarlo como analítica completa de adjudicaciones, se debe definir una clave única específica del reporte.

## Desplegar la actualización en Render

1. Hacé una copia de seguridad de la base PostgreSQL si los datos actuales son importantes.
2. Sustituí los archivos del repositorio GitHub por los de este ZIP (sin cambiar el nombre de proyecto/servicio), hacé commit y push.
3. En Render, desplegá el último commit (`Deploy latest commit` si no se despliega automáticamente). El programa crea la nueva tabla `sync_state` sin borrar `tenders`, `users` ni `sync_runs`.
4. Conservá las variables `DATABASE_URL`, `SECRET_KEY`, `REFRESH_TOKEN`, `ADMIN_PASSWORD_1`, `ADMIN_PASSWORD_2` y `USER_PASSWORD_1` que ya tenías en Render. **No publiques sus valores en GitHub**.
5. Entrá como administrador y pulsá **Sincronizar todas las páginas**. Mantené abierta la pestaña mientras se procesan bloques; si se interrumpe, vuelve a pulsar el botón y continuará en la última página persistida.

**No es necesario borrar la base de datos**. **No** cambies la URL de PostgreSQL si querés conservar los usuarios y registros existentes.

## Configuración opcional

| Variable | Valor por defecto | Propósito |
|---|---:|---|
| `SICOP_OPEN_DATA_URL` | URL pública del reporte | Dirección de la fuente, si cambia oficialmente |
| `SICOP_PAGES_PER_RUN` | `20` | Páginas máximas por invocación HTTP, entre 1 y 100 |
| `SICOP_SYNC_MAX_SECONDS` | `95` | Tiempo máximo de una invocación para respetar el timeout de Render |
| `MAX_CSV_UPLOAD_MB` | `70` | Tamaño máximo del CSV cargado desde la interfaz |

**Nota:** el reporte de SICOP suele mostrar 10 filas por página. Por ejemplo, 100 páginas equivalen a unas 1.000 filas y requieren cinco solicitudes de 20 páginas cada una.

## Actualización automática

Se conserva `POST /tasks/refresh` con la cabecera `Authorization: Bearer <REFRESH_TOKEN>`.

```bash
curl -X POST 'https://TU-SERVICIO.onrender.com/tasks/refresh' \
  -H "Authorization: Bearer TU_REFRESH_TOKEN"
```

El resultado JSON indica `status`, `processed`, `pages_processed`, `next_page`, `total_pages`, `source_results` y `stored`. Mientras `status` sea `partial`, seguí invocando el endpoint mediante tu programador de tareas. Al recibir `complete`, ya se terminó un recorrido de las páginas que ofreció el reporte y el siguiente llamado comenzará desde la página 1, actualizando datos existentes.

Un `status: error` no debe interpretarse como una sincronización terminada. Los registros y el cursor confirmados antes del error permanecen guardados.

El botón del panel encadena las llamadas automáticamente desde el navegador. Para funcionamiento desatendido incluso con la pestaña cerrada, configurá un programador externo que invoque `/tasks/refresh` con regularidad; Render no ejecuta por sí mismo el cron solo por existir ese endpoint.

## Consultas y exportación

- `GET /?page=1&per_page=50&q=texto&status=...&institution=...` — filtros y tabla paginada.
- `GET /api/tenders?page=1&per_page=100` — JSON con `items`, `total`, `page`, `per_page`, `total_pages` (máximo 500 filas por respuesta). **Cambia** la forma del JSON anterior, que era un arreglo sin metadatos.
- `GET /export.csv` — CSV descargable, sin límite de filas; admite los mismos filtros `q`, `status` e `institution`.
- `POST /admin/sync/reset` — vuelve el cursor de sincronización a la página 1 **sin borrar datos**.
- `POST /admin/import` — CSV de SICOP en formato de columnas compatibles con el reporte.

## Pruebas

```bash
pip install -r requirements.txt pytest
pytest -q
```

Las pruebas usan datos de ejemplo; **no aseguran** que el sitio de SICOP responda en tiempo real. Se recomienda revisar el registro de sincronización después de cada despliegue, porque el reporte oficial puede cambiar el HTML, la paginación o el acceso desde servidores externos.

## Actualización del lector de tablas (octubre de 2026)

Se corrigió el caso en que SICOP reporta resultados pero las filas contienen
celdas decorativas adicionales respecto del encabezado (íconos de ordenamiento,
columnas extra o encabezados divididos). La lectura ya no exige que el número de
celdas sea exactamente igual en ambas filas: alinea los campos usando el
número de procedimiento y la estructura documentada del reporte.

La corrección evita contar como éxito una respuesta de error, un bloqueo HTTP
403 o una página distinta de la solicitada. **No supera restricciones de
SICOP ni comprueba disponibilidad desde Render:** si el servidor oficial
bloquea accesos automatizados, usar su descarga oficial CSV sigue siendo la
alternativa soportada, sin borrar PostgreSQL.

### Ajuste de robustez del recorrido (octubre de 2026)

Las firmas de páginas ahora se calculan sobre todas las columnas de contenido (sin el número de fila visible), para evitar confundir dos páginas legítimas con registros idénticamente identificados. Una página verdaderamente repetida provoca reintentos limitados y un error seguro, en lugar de saltarse datos. Opcionalmente, `SICOP_PAGE_DELAY_SECONDS` agrega pausa entre peticiones. Ver `README_RENDER.md`.

### Cliente solicitante en el listado

El primer campo visible identifica **Cliente / institución solicitante**, que corresponde a la institución pública que contrata (`Institución` en el reporte de SICOP). El adjudicatario sigue identificado por separado, como proveedor. Se mantiene el filtro por cliente y se aceptan alias de esa columna en importaciones CSV. No requiere migración de base de datos.

## Filtros de oportunidades de tecnología (perfil Noventiq)

En el buscador, usá **Categoría tecnológica** para escoger «Todas las oportunidades TI · perfil Noventiq» o una especialidad (Microsoft, Google, AWS, nube, licencias, ciberseguridad, hardware, redes, software y soporte TI). También hay un acceso directo **Ver solo oportunidades TI (Noventiq)**.

El filtro funciona sobre **las filas ya almacenadas**, sin borrarlas ni alterar la base de datos. Se puede combinar con Estado, Cliente y búsqueda de texto; el filtro se conserva al paginar, al consultar `GET /api/tenders?tech=noventiq` y al usar `GET /export.csv?tech=noventiq`.

El perfil es una **selección editorial de palabras clave** en el objeto/descripcion de la compra, no una clasificación oficial de SICOP ni una afirmación de que Noventiq pueda ofrecer cada producto. Requiere revisar condiciones, requisitos y fechas del cartel. La clasificación es aproximada: puede incluir resultados no aplicables u omitir títulos muy genéricos. Modificá las listas de términos en `app/technology.py` si querés ajustarlo a tu catálogo comercial. Para que aparezcan más oportunidades, primero es necesario que la sincronización o la carga de CSV incorpore esas licitaciones; el filtro no obtiene registros nuevos.
