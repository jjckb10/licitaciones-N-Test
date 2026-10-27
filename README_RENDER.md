# Despliegue Render — SICOP Opportunity Hub V3

Este proyecto usa **PostgreSQL** para persistencia, y `gunicorn` en Docker. El archivo `render.yaml` crea el servicio web y la base de datos en una instalación nueva.

## Si ya tenés la app funcionando

1. No elimines la base de datos ni el servicio actual.
2. Subí a GitHub los archivos actualizados y desplegá el último commit en Render.
3. Conservá las variables existentes `DATABASE_URL`, `SECRET_KEY`, `REFRESH_TOKEN`, `ADMIN_PASSWORD_1`, `ADMIN_PASSWORD_2` y `USER_PASSWORD_1`.
4. La tabla nueva `sync_state` se creará automáticamente. Los registros anteriores se conservan.
5. Presioná «Sincronizar todas las páginas», manteniendo abierta la página hasta completar el recorrido.
6. Si el origen responde con un error, revisá el mensaje y usá la importación de CSV oficial como alternativa.

## Parámetros nuevos opcionales

- `SICOP_PAGES_PER_RUN=20` (cantidad máxima de páginas por llamada).
- `SICOP_SYNC_MAX_SECONDS=95` (tiempo máximo de una llamada; debe ser menor al timeout de Gunicorn).

### Para programar actualizaciones

Invocá periódicamente `POST /tasks/refresh` con `Authorization: Bearer <REFRESH_TOKEN>`. Repite hasta obtener `"status":"complete"`. Un resultado `"partial"` significa que la tarea guardó datos pero todavía tiene páginas pendientes.

### Advertencia sobre alcance

La página «Datos abiertos concursos» no es un índice exhaustivo de procesos en recepción de ofertas. La nueva sincronización obtiene todas las páginas disponibles de **ese reporte**, no toda la información que existe en el sistema SICOP.
