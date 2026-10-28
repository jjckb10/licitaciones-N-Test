# SICOP Opportunity Hub V6.1 — Interfaz simplificada

## Resumen

La V6.1 parte del ZIP completo V6 de administración de usuarios. Reorganiza los filtros, sin reemplazar las funciones de SICOP, PostgreSQL, clasificación Noventiq, gestión de usuarios ni sincronización.

### Qué cambia en la pantalla

1. Un formulario principal para todo: búsqueda global, perfil comercial, orden y cantidad de filas. Botón **Buscar**.
2. El botón **Filtros por columna** abre/cierra una fila con diez campos de refinamiento bajo las cabeceras de la tabla. Los filtros se combinan y operan sobre **toda la base**, no solamente la página actual.
3. El perfil permite tres vistas rápidas: Tecnología Noventiq (predeterminada), **Seguridad TI (general)** (SOC, SecOps, SIEM, IAM, MDR, vulnerabilidades, gobierno, etc.), y todas las licitaciones. Para buscar una subárea particular, desplegá los filtros de tabla y elegila en **Especialidad**.
4. Los filtros activos aparecen como etiquetas que se pueden quitar de una en una. **Limpiar filtros** restaura el perfil Noventiq.
5. Se eliminaron los dos formularios simultáneos, los filtros de estado/cliente/prioridad duplicados y los múltiples accesos rápidos de seguridad.
6. La sección de sincronización se puede expandir si sos administrador. Los enlaces a usuarios permanecen en el encabezado.

### Instalación

- **Opción A**: Si ya tenés V6 en GitHub, copiá los archivos incluidos en `SICOP_V6_1_Interfaz_actualizacion.zip`, respetando las carpetas. Hacé commit/merge y desplegá el último commit en Render.
- **Opción B**: Si necesitás todo el repositorio, utilizá `SICOP_Opportunity_Hub_V6_1_completo.zip`.
- No borres ni recrees la base de datos PostgreSQL. No modifiques `DATABASE_URL`, `SECRET_KEY` o credenciales de usuarios.
- La migración de base no cambia; **esta actualización es de vista y lógica de filtros**.
- En la aplicación, comprobá que aparezca **V6.1** en el encabezado y probá buscar `Microsoft`, filtrar por `SOC` y exportar CSV.

### Compatibilidad

- Se conservan todos los parámetros de búsqueda existentes en el backend, incluidos URLs antiguos con `status`, `institution`, `priority` o `column_area`. La interfaz nueva muestra los filtros anteriores como etiquetas y permite quitarlos.
- `GET /api/tenders` y `GET /export.csv` siguen aplicando la misma lógica de filtros del backend, incluido `tech=security_all`.
- La fecha de publicación **solo** aparece si la fuente la suministró; no se inventa una fecha de sincronización.
- No se modifica el extractor/paginación de SICOP: si la fuente bloquea o repite una página, el problema de origen permanece. La mejora visual no pretende resolverlo.

### Validación local

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q
```

Pruebas disponibles: 55 aprobadas, 2 omitidas al no estar Flask disponible en el entorno de preparación. Se renderizó la plantilla con Jinja y se verificó con navegador de escritorio y móvil que el panel de filtros se puede abrir/cerrar. Falta validarlo después del despliegue real de Render.
