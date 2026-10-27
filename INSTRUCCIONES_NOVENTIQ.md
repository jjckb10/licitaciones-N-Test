# Actualización: filtros tecnológicos para Noventiq

## Actualizar el repositorio

Estas modificaciones se prepararon sobre el código vigente de `main` en GitHub al momento de la revisión del 27 de octubre de 2026.

Para una actualización puntual, **reemplazá / agregá** exactamente estos archivos manteniendo las rutas:

- `app/technology.py` **NUEVO**: perfiles y palabras clave de tecnología.
- `app/main.py` **REEMPLAZAR**: añade `tech` a la búsqueda, API y CSV.
- `app/templates/index.html` **REEMPLAZAR**: selector, botón rápido y columna de categoría.
- `tests/test_technology.py` **NUEVO**: pruebas automatizadas.
- `README.md` y `README_RENDER.md` **REEMPLAZAR**: documentación de uso y despliegue.

Los otros archivos del proyecto quedan intactos. **No** elimines la base de datos, ni las variables de entorno o los usuarios de Render.

### Qué cambia en la interfaz

1. Un selector `Categoría tecnológica` con la opción "Todas las oportunidades TI · perfil Noventiq" y áreas específicas.
2. Un botón `Ver solo oportunidades TI (Noventiq)`.
3. Una nueva columna de `Área tecnológica detectada` con hasta dos etiquetas por registro.
4. Filtra todas las páginas, los contadores y el CSV; no solo lo que se ve en pantalla.

### Endpoints

- Panel: `/?tech=noventiq` o `/?tech=microsoft`.
- JSON: `/api/tenders?tech=cloud` (la respuesta incluye el filtro y `tech_areas` de cada elemento).
- CSV: `/export.csv?tech=noventiq`.

Admite mezclarlo con `q`, `status` e `institution`.

### Test

Con las dependencias instaladas:

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q
```

En la revisión local se obtuvieron **21 pruebas aprobadas, 1 omitida**. La prueba omitida requiere Flask, que no está instalado en este entorno; la validación final en Render sigue pendiente.

### Alcance y limitaciones

El filtro solo actúa sobre los **registros ya almacenados**. No resuelve por sí solo los errores de extracción/paginación de SICOP ni agrega nuevas fuentes. Es una categorización por palabras clave, no una evaluación automática de elegibilidad comercial: un título genérico puede quedar fuera y uno ambiguo puede entrar. Revisá siempre el cartel, la documentación y las fechas reales en SICOP.

### Próximo paso

Subí los archivos a GitHub, creá un Pull Request contra `main`, hacé merge y confirmá que Render desplegó el último commit. Comprobá el filtro tecnológico `Noventiq` y contrastá algunas filas con el cartel en SICOP.
