# Despliegue Render — SICOP Opportunity Hub V4 — Noventiq

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

## Diagnóstico de extracción HTML (revisión 27-oct-2026)

- El extractor V3 anterior podía descartar filas válidas al exigir que las filas y los encabezados tuvieran **idéntica** cantidad de celdas HTML.
- `app/sicop_source.py` ahora detecta el número de procedimiento y alinea las columnas aunque existan celdas decorativas adicionales. También tiene un segundo método para tablas con encabezados separados.
- Si SICOP devuelve HTTP 403, una página con «ID de soporte» o una sesión sin acceso, **ningún cambio del parser puede garantizar la descarga**. El panel mostrará un error descriptivo y conservará el cursor y los registros previos. Descargá el CSV desde el portal oficial e importalo mediante «Importar CSV oficial», o solicitá acceso legítimo a SICOP para el servidor de Render.
- Si SICOP ignora `page_no` y devuelve otra página, la sincronización se detiene sin declarar un recorrido completo.
- La extracción sigue limitada al reporte «Datos abiertos concursos». No equivale al listado de ofertas abiertas.

Para verificar el parser sin consultar el sitio real:

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q tests/test_sicop_source.py
```

## Corrección de páginas repetidas (27 de octubre de 2026)

- `app/sicop_source.py` compara el **contenido completo del registro**, no solo procedimiento/partida/línea/adjudicatario/estado. Esto evita falsos positivos cuando dos páginas comparten identificadores, pero cambian sus montos o descripciones.
- Si SICOP entrega realmente la misma página dos veces, hace dos reintentos espaciados con cabeceras `no-cache` y parámetro de verificación; si persiste, la sincronización **se detiene y conserva el avance**. No considera completa la importación y no inventa datos.
- `SICOP_PAGE_DELAY_SECONDS=0.5` (configuración opcional en Render) reduce la frecuencia de las consultas; admite entre 0 y 5 segundos.
- Con errores de acceso desde SICOP (HTTP 403/soporte), la extracción no se puede garantizar. Usá **Importar CSV oficial** desde el panel si el portal web permite descargarlo.
- Tras desplegar, iniciá la sincronización y verificá el mensaje del último intento; no reiniciés el recorrido a menos que quieras volver a partir de la página 1. Los registros existentes no se borran.

## Campo «Cliente / institución solicitante»

La tabla muestra primero la **entidad contratante** (el cliente que solicita el servicio). SICOP la identifica como `Institución`. También se aceptan nombres alternativos en CSV como `Entidad contratante`, `Institución contratante`, `Nombre de la institución`, `Comprador` o `Cliente`. **No confundir con `Adjudicatario`:** este último es el proveedor elegido, no el cliente. La información se guarda en la columna `institution` existente, sin modificar el esquema de PostgreSQL.

## Nuevo filtro: oportunidades TI y perfil Noventiq

Esta versión agrega `app/technology.py` y modifica `app/main.py` y `app/templates/index.html`.

- **No necesita variables de entorno ni cambios de esquema PostgreSQL.**
- Después del despliegue, el selector **Categoría tecnológica** incluye Noventiq, Microsoft, Google, AWS, cloud, licencias, ciberseguridad, hardware, redes, software y servicios TI.
- La API acepta `?tech=noventiq` y `?tech=microsoft`, etc. La exportación CSV respeta la misma selección.
- Las búsquedas solo abarcan lo guardado en la tabla `tenders`: no solucionan por sí solas errores de extracción del origen SICOP.
- Para pruebas, ejecutá `pytest -q` con las dependencias del proyecto y `pytest` instalados.


## V4: migración, extracción y actualización automática

Las columnas `is_it`, `it_score`, `it_priority`, `it_labels` se agregan automáticamente sin borrar datos.
El panel abre en `/?tech=noventiq`, usa las columnas nuevas para filtrar y muestra
las descripciones resumidas, el cliente validado y la prioridad comercial.
Para sincronización diaria desde GitHub Actions, configurar los secretos
`SICOP_APP_URL` y `SICOP_REFRESH_TOKEN` y habilitar Actions. Ver `V4_DESPLIEGUE.md`.
No confundir la institución con `Tipo Procedimiento`, ni el puntaje comercial
con una aprobación automática de participación o disponibilidad de ofertas.

## Actualización V5

La migración automática agrega `tenders.publication_date` y `tenders.classification_version` sin eliminar datos existentes. Las nuevas categorías de SOC, SecOps, SIEM, IAM, MDR/MSSP, vulnerabilidades y gobierno de seguridad se recalculan por lotes. No hay variables de entorno nuevas obligatorias. Para que se muestre una fecha de publicación debe existir explícitamente en la fuente o un CSV oficial compatible (ver `V5_DESPLIEGUE.md`).

## V6 — Gestión de usuarios

No se modifican credenciales ni datos existentes al actualizar. La pantalla `/admin/users` permite gestionar nuevas cuentas y contraseñas sin editar Render. Se añade migración aditiva a `users`, protección CSRF y revocación de sesiones por cambios de rol/contraseña/estado. **Tras el despliegue volvé a iniciar sesión**. Conservá el `SECRET_KEY` de Render y no modifiques `DATABASE_URL`. Guía completa: `V6_USUARIOS_DESPLIEGUE.md`.
