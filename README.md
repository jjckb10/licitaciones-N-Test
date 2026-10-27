# SICOP Opportunity Hub

MVP interno desplegable en Azure App Service. Incluye inicio de sesión corporativo mediante App Service Authentication / Microsoft Entra ID, buscador, filtros, importación CSV oficial, sincronización de la página pública, API JSON y persistencia SQLite sobre el almacenamiento de App Service.

## Requisitos para desplegar
- Suscripción de Azure con permisos para crear Resource Group, Azure Container Registry y App Service.
- Azure CLI y Docker, o Azure Cloud Shell. No necesitas un editor local: abre Azure Cloud Shell, carga este ZIP, descomprímelo y ejecuta los comandos.

## Despliegue desde Azure Cloud Shell
```bash
unzip SICOP_Hub_Azure.zip -d sicop-hub
cd sicop-hub
export RG="rg-sicop-hub"
export LOCATION="eastus2"
export APP="sicop-hub-empresa-001"
export ADMIN_USERS="tuusuario@tuempresa.com"
./deploy.sh
```

El script crea ACR, compila la imagen, crea App Service B1, configura almacenamiento persistente y activa el login de Microsoft.

## Primer uso
1. Ingresa con tu cuenta corporativa.
2. Pulsa **Sincronizar página pública**.
3. Si el portal cambia o bloquea la lectura directa, descarga el CSV en la página oficial de Datos abiertos concursos y usa **Importar CSV oficial**.

## Seguridad
- No hay claves de SICOP incluidas ni requeridas.
- El sitio obliga autenticación de Microsoft Entra ID.
- `ADMIN_USERS` controla quién puede sincronizar/importar, separado por comas.
- Para producción avanzada, sustituye SQLite por Azure Database for PostgreSQL y usa Managed Identity.

## Variables
- `DATABASE_URL`: conexión SQLAlchemy. Predeterminado SQLite persistente.
- `ADMIN_USERS`: correos administradores separados por comas.
- `SECRET_KEY`: secreto de sesión.
- `SICOP_OPEN_DATA_URL`: fuente pública configurable.

## Límites conocidos
El portal oficial de datos abiertos ofrece CSV/XLS y una tabla pública. La estructura o los parámetros de su interfaz pueden cambiar. Por eso este MVP conserva la importación CSV oficial como ruta segura y encapsula la fuente para reemplazarla posteriormente por otra API/integración sin rehacer la interfaz.
