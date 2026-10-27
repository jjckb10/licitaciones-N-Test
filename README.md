# SICOP Opportunity Hub V2

## Usuarios
Administradores: juanjose.solanocamacho@noventiq.com y jjckb@gmail.com.
Usuarios con USER_PASSWORD_1: alonso.fallasvargas@noventiq.com, diana.diaz@noventiq.com, juan.marincabezas@noventiq.com, a.wong@noventiq.com y ziomara.chavarria@noventiq.com.

## Render
Sube el contenido a GitHub. En Render selecciona New > Blueprint, conecta el repositorio y despliega render.yaml. Render pedirá ADMIN_PASSWORD_1, ADMIN_PASSWORD_2 y USER_PASSWORD_1.

## Variables
DATABASE_URL es creada por el Blueprint desde PostgreSQL. SECRET_KEY y REFRESH_TOKEN se generan. Define las tres contraseñas como secretos.

## Refresh automático
Endpoint: POST /tasks/refresh. Header: Authorization: Bearer valor-de-REFRESH_TOKEN. Usa un monitor/scheduler HTTP externo o un Cron Job compatible para invocarlo. La lectura directa procesa la tabla pública que SICOP entregue; para una carga histórica exhaustiva importa los CSV oficiales disponibles.

## Cambio de contraseñas
Las variables crean usuarios ausentes. Por seguridad, cambiar una variable no reemplaza automáticamente un hash ya existente en PostgreSQL. Para reiniciar claves durante el piloto, elimina las filas de users o recrea la base. Una futura pantalla de administración puede implementar restablecimiento individual.
