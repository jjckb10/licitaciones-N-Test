# Despliegue en Render

Usuarios permitidos:
- juanjose.solanocamacho@noventiq.com — admin
- jjckb@gmail.com — admin
- alonso.fallasvargas@noventiq.com — usuario

## Variables secretas
En Render define ADMIN_PASSWORD_1, ADMIN_PASSWORD_2 y USER_PASSWORD_1 con contraseñas fuertes. No las guardes en GitHub. SECRET_KEY se genera automáticamente con Blueprint.

## Deploy
1. Sube este contenido a un repositorio privado de GitHub.
2. En Render crea un Blueprint y selecciona el repositorio. render.yaml configura el servicio.
3. Render solicitará las tres variables de contraseña marcadas sync:false.
4. Despliega y abre la URL que Render te asigne.

## Nota de persistencia
El plan gratuito no debe considerarse almacenamiento persistente para SQLite. Esta configuración sirve para validar el MVP. Para conservar usuarios/datos entre recreaciones del servicio, conecta una base PostgreSQL externa/persistente y cambia DATABASE_URL.

## Roles
Admin puede sincronizar/importar SICOP. Usuario puede consultar y filtrar.
