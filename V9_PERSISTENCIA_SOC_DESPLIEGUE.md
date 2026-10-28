# SICOP Opportunity Hub V9 – datos persistentes y radar SOC

## 1. Por qué parecían desaparecer los registros en Render

La V8 mantiene su información en `tenders` y `observatory_tenders` mediante `INSERT ... ON CONFLICT`; **no ejecuta un `DELETE` ni limpia las tablas al iniciarse**. Sin embargo, si `DATABASE_URL` no estaba configurada, el código utilizaba `sqlite:////tmp/sicop.db`. En Render gratuito `/tmp` es efímero: desaparece al reiniciar, hibernar o desplegar. **V9 bloquea ahora ese fallback en Render**, en vez de mostrar un panel vacío como si todo estuviera normal.

No podemos saber cuál fue el problema real sin ver Render > Environment y la base que está conectada. Otras causas: DB gratuita que venció, un servicio de Render nuevo conectado a otra DB, URL antigua/errónea, o tabla que está vacía. La propia base de Render gratuita **expira a los 30 días**; después hay 14 días para actualizarla antes de que se borre. Render no proporciona copias de seguridad administradas en ese nivel. Por tanto **un cambio de código no puede garantizar conservación indefinida con el PostgreSQL gratuito**.

## 2. Qué verificar antes de desplegar

1. Hacé respaldo de tu PostgreSQL actual (por ejemplo, con `pg_dump` desde un equipo autorizado, sin publicarlo en GitHub). `Exportar CSV` solo respalda información de licitaciones; **no** equivale a una copia completa de cuentas, cursores ni datos operativos.
2. Render > servicio web > **Environment**: `DATABASE_URL` debe apuntar a la misma base PostgreSQL que contiene los registros. No pegues su contenido en la conversación ni en el código.
3. Render > PostgreSQL: comprobar el estado, plan y fecha de vencimiento. Para conservar datos a largo plazo, migrar a PostgreSQL de pago o a un PostgreSQL externo que admita persistencia, con respaldos. Evitar el SQLite dentro del web service gratuito.
4. Mantener `SECRET_KEY` y `REFRESH_TOKEN` anteriores. No crear una base nueva como parte del despliegue.
5. Asegurar `REQUIRE_PERSISTENT_DB=1` en las variables de entorno. El `render.yaml` de V9 ya lo incluye para nuevos despliegues Blueprint; si el servicio fue creado manualmente, agregarlo a mano.
6. Tras desplegar: visitar `/health` y confirmar `"database":"postgresql"`, `"persistent_storage":true` y los conteos `sicop_rows` y `observatory_rows`. Este endpoint publica contadores, **no** contraseñas o URL. Si falla el arranque con mensaje de `DATABASE_URL`, verificar la configuración antes de continuar.

> Atención: si la base anterior **ya fue eliminada**, V9 no la recupera mágicamente. Se necesita un respaldo o reimportar archivos oficiales.

## 3. Nueva opción «Actualizar novedades sin reiniciar»

- Botón en Administración > Sincronización SICOP.
- Inspecciona, como máximo, los archivos del mes corriente y del mes previo del Observatorio. Procesa **un mes por llamada** para respetar los límites de Render. Los archivos históricos anteriores permanecen sin tocar.
- Si un mes fue revisado recientemente, lo omite durante `OBS_REFRESH_HOURS=18` (configurable entre 1 y 168 horas).
- Compara las licitaciones de cada ZIP con PostgreSQL: inserta procedimientos nuevos, actualiza los que cambiaron y no reescribe registros idénticos. El panel reporta **nuevos, modificados y sin cambios**.
- El botón «Recorrer histórico mes a mes» sigue disponible solo para la carga inicial; si ya importaste los meses, los omite.
- `POST /tasks/observatorio` con token, sin parámetro `month`, también ejecuta la actualización incremental. Si se pasa `?month=AAAAMM`, importa ese mes explícitamente. Requiere un programador externo; el servicio web no ejecuta tareas automáticamente por sí solo.
- El reporte HTML de SICOP sigue disponible como fuente complementaria, pero puede repetir páginas o bloquear a Render. **No** se debería recorrer por completo cada vez que el servicio despierta.

### Alcance real

El Observatorio ofrece ZIP mensuales, **no** una API de delta por registro. Para detectar cambios del mes vigente, todavía hay que descargar su ZIP una vez por revisión y comparar sus procedimientos localmente; V9 evita volver a recorrer *todos* los meses históricos. No garantiza conocer el estado de ofertas abiertas en tiempo real: las vigencias históricas siguen por verificar en SICOP.

## 4. Guía del compañero incorporada sin reemplazar la clasificación anterior

Se conserva el clasificador tecnológico previo Noventiq (Microsoft, Azure, Google, AWS, licenciamiento, seguridad, hardware y software) y se agrega `app/soc_radar.py` con niveles:

| Nivel | Señales en descripción | Interpretación |
|---|---|---|
| SOC explícito | SOC, Centro de Operaciones de Seguridad, Security Operations Center | Mención directa |
| SOC posible · funciones equivalentes | Contexto de infraestructura TI o ciberseguridad **y** funciones de monitoreo **y** respuesta/mitigación | Posible SOC, leer pliego |
| Seguridad relacionada · revisar | Firewall, EDR/XDR, vulnerabilidades, IAM, hardening, NOC, etc. | Complementario; no equivale automáticamente a SOC |
| Sin evidencia SOC | Solo CCTV/guardas/vigilancia física u objetos no tecnológicos | No marcar como SOC |

La búsqueda añade «Mapa SOC» para ver conjuntos directos, equivalentes y complementarios y «Seguridad complementaria» para revisar compras adyacentes. Las celdas de área incluyen explicación de señales. Nunca se inventan requisitos del cartel. La captura aportada por el equipo es de baja resolución, por lo que se incorporó su **enfoque de niveles**, sin afirmar que todas sus frases se hayan podido transcribir literalmente.

## 5. Instalación

Reemplazar/subir los archivos del **proyecto completo V9** al repositorio existente, respetando rutas, y hacer merge en `main`. La última rama pública revisada todavía no contenía todos los módulos de V8, así que si se usa el paquete parcial se pueden omitir dependencias. El ZIP completo es más seguro.

No cambiar `DATABASE_URL`, eliminar servicios de PostgreSQL ni resetear el historial. El esquema V9 agrega solamente columnas de contadores a `observatory_imports`, sin borrar tablas. Ejecutar `python -m pytest -q` con las dependencias del proyecto y pytest; probar `/health` en Render.
