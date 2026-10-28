# SICOP Opportunity Hub V5 – Noventiq | Implementación

## Cambios de V5

1. Tabla con columna **Fecha de publicación**, distinta de `first_seen` (fecha de la primera importación) y de `last_seen` (última sincronización). La fecha **nunca se infiere del año del procedimiento ni del servidor**. El campo `publication_date` se guarda como ISO `YYYY-MM-DD` y aparece como `DD/MM/YYYY` en pantalla.
2. **Filtros por cada columna** (cliente, descripción, publicación desde/hasta/sin fecha, prioridad, categoría, procedimiento, estado, tipo, monto mínimo/máximo y adjudicatario). Se aplican a la **base de datos completa**, no solo a la página visible. Son compatibles con la paginación, API y exportación CSV.
3. Nuevas áreas de seguridad: **SOC**, **SecOps**, **SIEM/SOAR**, **IAM/PAM**, **MDR/MSSP**, **vulnerabilidades/pentesting** y **gobierno de seguridad/SGSI**. También incluye términos de administración de seguridad informática y operación de seguridad. Los filtros son orientativos y deben validarse contra el pliego.
4. Ordenamiento por relevancia, publicación más reciente, monto o cliente.
5. Migración automática y conservadora de PostgreSQL: incorpora `publication_date` y `classification_version`, sin borrar registros, y reclasifica los existentes en bloques de hasta 500 por solicitud. La clasificación se persiste tras importar CSV o sincronizar.

## Cómo subir a GitHub y Render

1. Hacé respaldo de PostgreSQL si tiene registros importantes.
2. Subí los archivos del ZIP de actualización **respetando sus rutas** y hacé merge en `main`.
3. Esperá que Render despliegue el commit correspondiente (o seleccioná *Deploy latest commit*).
4. Entrá como administrador y abrí el panel. La aplicación añadirá automáticamente las nuevas columnas sin destruir los datos existentes.
5. Probá `SOC` desde los filtros rápidos y luego los filtros por columna; el botón **Aplicar filtros de tabla** filtra toda la base.
6. Probá `Descargar CSV filtrado`: exporta los mismos criterios aplicados. El CSV ahora agrega `publication_date` y `classification_version`.

## De dónde sale la fecha de publicación

El reporte actual `RE_DatosAbiertosConcursosView.jsp` normalmente NO expone la fecha de publicación en las columnas de su tabla. Tiene filtros de fechas, pero **tener un filtro de fecha no significa que el campo exista en cada fila**.

V5 reconoce un campo explícito en HTML o CSV con encabezados tales como `Fecha de publicación`, `Fecha/hora de publicación`, `Fecha y hora de publicación`, `FECHAH_PUBLICACION`, `publication_date` o `published_at`, entre otros. Se admiten `DD/MM/YYYY HH:MM`, `YYYY-MM-DD HH:MM`, ISO 8601 y fechas comunes equivalentes.

- Si el origen no incluye fecha, se mostrará **No disponible**. Esto es correcto: no representa un error de importación.
- Si tenés una exportación oficial que contiene fechas de publicación y los números de procedimiento/partidas/líneas, importala desde **Importar CSV oficial**. Los registros existentes se actualizan según su clave de procedimiento, partida y línea.
- Si luego se sincroniza el reporte HTML sin fechas, el UPSERT conserva las fechas previamente conocidas.
- El filtro de fechas mostrará solo registros con fecha real; podés activar **Sin fecha** para encontrar registros incompletos.
- **No se implementó una consulta adicional** al buscador de concursos de SICOP que resuelva fechas para cada número de procedimiento; esa integración requiere verificar el mecanismo oficial de consulta y las condiciones de acceso.

## Límites conocidos

- El historial completo y las ofertas vigentes no son equivalentes: la fuente usada sigue siendo `Datos abiertos concursos`.
- **No resuelve** por sí mismo el problema previo de páginas repetidas o restricciones de SICOP desde Render; mantiene las protecciones contra duplicaciones y resultados falsos.
- Fechas de publicación no son fechas de cierre de ofertas. La app no debe inferir que un procedimiento sigue abierto solo por su fecha.
- Los importes pueden usar monedas diferentes; filtrar por monto no realiza conversión monetaria.
- El perfil Noventiq es una clasificación por palabras clave, no certifica compatibilidad comercial, admisibilidad ni disponibilidad para ofertar.
- No se modificaron usuarios, contraseñas ni configuración de conexión PostgreSQL.

## Pruebas

```sh
pip install -r requirements.txt pytest
python -m pytest -q
```

Se verificaron **46 pruebas automáticas**, incluyendo parsing de fechas, clasificación SOC/SIEM/MDR, SQL de filtros, plantilla y UPSERT conservador. Una prueba anterior de integración con Flask se omite en el entorno de generación porque Flask no está instalado; Render sí debe instalar las dependencias mediante `requirements.txt`. No se ha probado el despliegue real ni la respuesta en vivo de SICOP.
