# V8 — Sincronización multifuente y búsqueda SOC para Noventiq

## Qué resuelve

El reporte de SICOP `RE_DatosAbiertosConcursosView.jsp` puede devolver solo diez registros por página y, desde Render, puede repetir páginas o devolver errores. La V8 **no salta las páginas ni intenta eludir los controles de SICOP**: conserva ese origen y añade otro canal oficial, los **ZIP mensuales del Observatorio de Compra Pública**.

Los ZIP contienen, entre otros, `DetalleCarteles.csv` e `InstitucionesRegistradas.csv`. Se leen en lotes sin extraer archivos en el disco ni enviar los datos a otros proveedores. Se usa `NRO_PROCEDIMIENTO` para deduplicar los carteles del Observatorio e identificar el cliente por `CEDULA_INSTITUCION`. Se importan `DESCRIPCION`, `FECHA_PUBLICACION` y `FECHA_CIERRE_RECEPCION` cuando están presentes.

**Los ZIP históricos no verifican por sí solos si una licitación recibe ofertas hoy.** En su lugar los procedimientos se marcan como **«Vigencia por verificar en SICOP»** y muestran la fecha de cierre reportada, que debe confirmarse en el expediente oficial (puede haber prórrogas).

Los registros antiguos de `tenders` **no se borran ni se sustituyen**. Se crea `observatory_tenders` y una vista de consulta SQL combinada que evita duplicar procedimientos entre ambos orígenes. El cliente, descripción, fecha de publicación y especialidades provienen preferentemente del cartel del Observatorio, cuando están disponibles.

## El nuevo filtro de SOC

En «Perfil comercial», seleccionar **«SOC y servicios equivalentes (monitoreo y respuesta)»**, sin escribir SOC en la búsqueda libre.

Incluye licitaciones que mencionan SOC explícitamente y señales asociadas a:

- **Centro de Operaciones de Ciberseguridad / Centro de Operaciones de Seguridad / NOC y SOC**.
- **Administración y respuesta de eventos de seguridad informática** y correlación de eventos (SIEM/SOAR).
- **Monitoreo, gestión y mitigación del riesgo en infraestructura tecnológica**.
- **Detección y respuesta a incidentes**, gestión de incidentes, monitoreo de amenazas, servicios administrados y monitoreo 24/7.
- **Plataformas y licenciamiento de monitoreo de ciberseguridad**.

La clasificación es **heurística y orientativa**. No equivale a leer todo el pliego y puede omitir descripciones genéricas. No trata de forma automática como SOC la seguridad física, la vigilancia con guardas, alarmas y CCTV. Los resultados de una coincidencia indirecta se identifican como «SOC potencial · servicios equivalentes» con la nota «revisar el pliego». Los filtros por especialidad, CSV y API también incluyen esta categoría.

## Instalación en el Render actual

1. Hacer respaldo de PostgreSQL. No borrar la base ni las variables `DATABASE_URL`, `SECRET_KEY` y contraseñas existentes.
2. Subir los archivos de la actualización a GitHub **respetando las carpetas** y fusionar en `main`. Render debe desplegar el último commit (automático o «Deploy latest commit»).
3. Entrar como administrador. Abrir **«Administración · Sincronización SICOP»**.
4. Primero pulsar **«Descargar mes oficial»** para el mes actual, por ejemplo `202610`, o importar manualmente un ZIP desde https://www.observatoriocomprapublica.go.cr/descargas-sicop/.
5. Para históricos, elegir mes inicial `202301` (o `201001` para intentar toda la serie disponible) y pulsar **«Recorrer histórico mes a mes»**. La aplicación procesa **un mes por petición** y conserva un cursor en PostgreSQL. La pestaña debe permanecer abierta para recorrer varios meses. El botón **«Reiniciar histórico desde ese mes»** cambia el cursor sin borrar datos.
6. En el panel, elegir **«SOC y servicios equivalentes»**. Para encontrar un número de procedimiento concreto, elegir «Todas las licitaciones» y buscar el número exacto, sin otros filtros.

La aplicación conserva `POST /tasks/refresh` para la fuente HTML anterior y agrega `POST /tasks/observatorio` (protegido con `Authorization: Bearer <REFRESH_TOKEN>`) para importar un mes oficial: `?month=AAAAMM`. Por ejemplo:

```bash
curl -X POST 'https://TU-SERVICIO.onrender.com/tasks/observatorio?month=202610' \
  -H 'Authorization: Bearer TU_REFRESH_TOKEN'
```

**No poner el token real en GitHub ni en capturas**. Para automatización fuera del navegador, configurar el programador autorizado y verificar el tiempo de respuesta de Render (un ZIP mensual grande puede sobrepasar el límite del servicio gratuito). La descarga fija desde Azure puede no ser accesible desde algunos entornos.

## Referencias oficiales

- Descargas mensuales del Observatorio: https://www.observatoriocomprapublica.go.cr/descargas-sicop/
- Diccionario SICOP para `DetalleCarteles`: https://www.sicop.go.cr/atDocs/Diccionario_de_datos_reporte_de_contratos_de_Datos_abiertos.pdf

## Comprobación y límites

```bash
pip install -r requirements.txt pytest
python -m compileall -q app
python -m pytest -q
```

Las pruebas de la V8 emplean **ZIP simulados con la estructura documentada**, no validan el acceso real desde Render ni que los archivos mensuales sigan siendo accesibles. Importaciones históricas grandes pueden tardar bastante; iniciá con un mes para comprobar tiempos y estructuras antes de recorrer toda la serie. Las fechas y los estados deben confirmarse en el SICOP oficial.
