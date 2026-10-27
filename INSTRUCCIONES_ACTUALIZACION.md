# Actualización SICOP Opportunity Hub — 27 de octubre de 2026

Este paquete contiene **solamente** los archivos que cambian respecto a la V3 ya fusionada en `main`.

## Archivos para reemplazar o agregar en GitHub

- `app/sicop_source.py` — firma completa de contenido y hasta 2 reintentos controlados de páginas duplicadas.
- `app/main.py` — pausa opcional entre páginas, cálculo fresco de la huella de la página anterior al retomar y alias para entidad contratante.
- `app/templates/index.html` — columna destacada «Cliente / institución solicitante», separada de «Proveedor adjudicado».
- `tests/test_sicop_source.py` — pruebas de paginación, firma y reintentos.
- `tests/test_app.py` — pruebas de sincronización y alias de cliente (requieren Flask instalado).
- `tests/test_template_client.py` — pruebas de visualización en la tabla.
- `README.md` y `README_RENDER.md` — documentación actualizada.

**No elimines ni reemplaces** `render.yaml`, `Dockerfile`, `requirements.txt` ni la base PostgreSQL; no están incluidos en el ZIP.

## Despliegue

1. En GitHub, creá una rama, subí los archivos respetando las rutas indicadas y fusioná los cambios mediante Pull Request.
2. Dejá que Render despliegue `main`; conservá todas las variables de entorno actuales.
3. Ingresá como administrador y seleccioná **Sincronizar todas las páginas**. Si queda un cursor parcialmente avanzado, el sistema revisará nuevamente la página previa.
4. Verificá que **Cliente / institución solicitante** muestre la organización pública que compra (por ejemplo, una municipalidad), mientras «Proveedor adjudicado» muestre el adjudicatario, si existe.

Variable de entorno opcional: `SICOP_PAGE_DELAY_SECONDS=0.5` (segundos de pausa entre páginas).

## Pruebas realizadas

- `python -m pytest -q`: 17 pruebas aprobadas; 1 módulo de pruebas de integración se omitió porque este entorno carece de Flask. Se comprobó sintaxis con `python -m compileall -q app tests`.
- **No se pudo validar contra el sitio SICOP ni contra Render en tiempo real.** SICOP devuelve un mensaje de bloqueo/ID de soporte desde herramientas externas. Por ello, los reintentos son una mejora de robustez, no una garantía de importación completa.
- Una página repetida tres veces sigue generando error. No se salta esa página para evitar pérdida de registros.

Si el problema persiste con estas correcciones, importá un CSV oficial del portal desde la app o verificá la navegación con herramientas de desarrollo en el navegador para determinar si SICOP requiere una sesión o formulario para pasar de página.
