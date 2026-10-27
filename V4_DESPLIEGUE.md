# SICOP Opportunity Hub V4 — Perfil Noventiq

## Cambios visibles

- Al abrir el dashboard (`/`), el filtro `tech=noventiq` está activo automáticamente. `tech=all` permite ver la base completa.
- En sincronización HTTP y CSV se guardan todas las filas con sus datos originales **más** clasificación comercial calculada de la descripción: `is_it`, `it_score`, `it_priority`, `it_labels`.
- Una migración automática y no destructiva agrega esas cuatro columnas en PostgreSQL. También reclasifica progresivamente 500 filas históricas por solicitud, hasta terminar; no requiere eliminar la tabla `tenders`.
- La tabla muestra el **cliente / institución solicitante** y una **breve descripción de la contratación**, sin confundirlos con el tipo de licitación o el adjudicatario. Si el nombre verdadero no está disponible en el origen, dice `Cliente no informado` en vez de inventar uno.
- La puntuación (0–100) es **heurística**: Alta/Media/Baja mide coincidencia con áreas de interés de Noventiq, **no** significa que la oferta esté abierta, ni que cumpla con los pliegos o que el valor sea conveniente.
- Filtros por tecnología, estado, cliente, prioridad y búsqueda se aplican igual en el panel, `/api/tenders` y `/export.csv`.

## Actualizar el proyecto existente en Render

1. Subir el contenido del ZIP a `jjckb10/licitaciones-N-Test`, respetando las rutas (en especial `app/presentation.py`, `app/technology.py`, `app/main.py`, `app/sicop_source.py`, `app/templates/index.html` y `.github/workflows/sicop-sync.yml`). Usar preferiblemente un Pull Request.
2. Verificar en Render el despliegue del nuevo commit y `GET /health`. **Conservar** `DATABASE_URL`, `SECRET_KEY`, contraseñas y `REFRESH_TOKEN` existentes. No publicar sus valores.
3. Ingresar al panel. Las oportunidades clasificadas aparecerán primero; `tech=all` mostrará todas las filas.
4. Pulsar «Sincronizar todas las páginas» o importar un CSV oficial para corregir registros viejos que tengan la institución o descripción mal alineada. **La migración no puede deducir un cliente que jamás llegó correctamente a PostgreSQL.**
5. En GitHub → repositorio → Settings → Secrets and variables → Actions, agregar dos secretos:
   - `SICOP_APP_URL`: dirección base HTTPS del servicio Render (sin `/` al final).
   - `SICOP_REFRESH_TOKEN`: exactamente el valor privado de `REFRESH_TOKEN` configurado en Render.
6. Activar GitHub Actions y probar manualmente el flujo `Sincronización diaria SICOP — Noventiq V4`. Si funciona, correrá todos los días a las **07:15 a. m. de Costa Rica**, pudiendo interrumpirse por bloqueos del origen.

## Límite de la fuente y seguridad

`Datos abiertos concursos` de SICOP **no equivale** al listado completo de contrataciones que aún reciben ofertas. La fuente tiene campos de adjudicación, y no siempre publica un plazo de cierre. La aplicación no inventa fechas ni estados y no garantiza poder recorrer una paginación que SICOP ignore o bloquee: al fallar conserva los datos y el cursor.

Si un nombre de institución fue mal importado (por ejemplo `LICITACIÓN REDUCIDA`) se oculta y se limpia al reclasificar; solo una nueva importación con el nombre auténtico lo restaura. Recomendado respaldo de PostgreSQL antes de actualizar una app en producción.

## Pruebas

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```
