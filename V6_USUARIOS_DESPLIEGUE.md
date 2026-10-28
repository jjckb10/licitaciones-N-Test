# V6 — Administración de usuarios desde el panel

## Instalación en Render

1. Hacé una copia de seguridad de PostgreSQL si tus registros son importantes.
2. Actualizá los archivos respetando las carpetas (`app/main.py`, `app/templates/index.html`, `app/templates/login.html` y el nuevo `app/templates/users.html`).
3. Subí el commit al repositorio y desplegalo en Render. Conservá `DATABASE_URL` y `SECRET_KEY` actuales.
4. Cerrá sesión y **volvé a iniciar sesión** una vez: por seguridad se invalidan las sesiones antiguas, que no incluyen versión de cuenta.
5. Abrí el botón **Administrar usuarios** en el encabezado o en la sección de sincronización.

## Funciones de administración

- Agregar un usuario con correo, contraseña inicial (mínimo 12 caracteres) y rol `Usuario` o `Administrador`.
- Restablecer contraseñas de cuentas existentes (no se muestran nunca contraseñas anteriores).
- Cambiar roles (los usuarios con rol `Usuario` consultan datos; los administradores pueden sincronizar, importar y administrar cuentas).
- Desactivar/reactivar accesos sin eliminar información o historial.
- Los cambios de rol, contraseña y estado invalidan las sesiones antiguas de ese usuario.
- Evita desactivarse a sí mismo y conservará por lo menos un administrador activo.

## Migración sin pérdida de cuentas

- La tabla `users` existente se conserva. Se añaden columnas `is_active`, `auth_version`, `created_at` y `last_login` si faltan.
- Las cuentas históricas creadas desde `ADMIN_PASSWORD_1`, `ADMIN_PASSWORD_2`, `USER_PASSWORD_1` **no se borran ni se alteran**.
- Se detiene la resincronización continua de cuentas y roles desde Render: **PostgreSQL pasa a ser la fuente de verdad**.
- En una instalación completamente nueva y vacía, las variables de Render permiten inicializar las cuentas heredadas **una sola vez**; es necesaria al menos una contraseña de administrador en las variables.
- Después de la inicialización, cambiar esas variables **no cambia las contraseñas** en la base. Usá el panel.
- `SECRET_KEY` debe ser una clave secreta larga y estable, no el valor predeterminado de desarrollo. No la cambies sin motivo, porque invalidaría todas las sesiones.

## Seguridad y uso

- Solo un administrador autenticado accede a `/admin/users`.
- Las contraseñas se guardan como hashes seguros de Werkzeug (no en texto plano).
- Hay protección CSRF para los formularios POST, incluido el inicio de sesión. La sincronización automatizada `/tasks/refresh` continúa usando `Authorization: Bearer REFRESH_TOKEN`.
- No compartás contraseñas por GitHub ni las guardés en archivos del repositorio. Compartí claves iniciales por un canal privado.
- Si cambiaste la contraseña de tu propia cuenta, el sistema te pedirá volver a ingresar.
- Si una acción da error 400 de CSRF, recargá la página y volvé a enviarla. Para las sesiones anteriores a V6, cerrá e iniciá sesión otra vez.

## Pruebas

```bash
pip install -r requirements.txt pytest
pytest -q
```

Las pruebas HTTP de administración requieren Flask y Werkzeug, incluidas en `requirements.txt` por la dependencia Flask. En este entorno de generación no se dispone de Flask para ejecutar las pruebas HTTP. Revisá los logs de Render después del despliegue.
