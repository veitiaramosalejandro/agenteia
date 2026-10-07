# Agent Control Center

Consola web para administrar instancias SolidSET, agentes especializados, prompts, modelos y pruebas de diálogo.

## Desarrollo

```bash
npm install
npm run dev
```

Vite publica la consola en `http://localhost:4173` y redirige `/api` a `http://localhost:8000`.

## Docker

```bash
docker compose -f docker-compose-dev.yml up -d --build agent-control-center
```

La consola queda disponible en `http://localhost:4173`.

## Alcance implementado

- Selector persistente y obligatorio de instancia.
- Panel de estado del runtime.
- Inventario de instancias y prueba de conectividad.
- Listado de agentes aislado por instancia.
- Generación, previsualización y publicación de prompts.
- Asignación de modelos y capacidades.
- Laboratorio de diálogo sin publicación en SolidSET.
- Inventario, alta, reindexación y desactivación de fuentes privadas.
- Ingestión observable del catálogo SolidSET por instancia.
- Prueba RAG aislada por instancia, agente y canal opcional.
- Administración de agentes y orden de respuesta por canal.
- Reglas con capacidades requeridas, aprobación y límites horarios.
- Evaluación y ejecución controlada con vista previa antes de publicar.
- Panel unificado de respuestas, herramientas, ingestiones y automatizaciones.
- Métricas de rendimiento, errores, filtros y exportación CSV segura.
- Autenticación administrativa con sesiones revocables.
- Roles de administrador, operador y auditor con permisos por operación.
- Aprobaciones persistentes, historial de cambios y restauración autorizada.

La ingestión del catálogo requiere la clave configurada en
`HISTORICAL_INGESTION_ADMIN_KEY`. La consola la solicita al operador y la
mantiene solo en memoria; no se guarda en `localStorage` ni se envía al backend
del frontend.

Las credenciales de proveedores nunca se muestran.

## Acceso administrativo

Antes del primer arranque configure estas variables en `.env`:

```env
CONTROL_CENTER_AUTH_ENABLED=true
CONTROL_CENTER_BOOTSTRAP_USERNAME=admin
CONTROL_CENTER_BOOTSTRAP_PASSWORD=una-clave-unica-de-al-menos-12-caracteres
CONTROL_CENTER_BOOTSTRAP_DISPLAY_NAME=Administrador
CONTROL_CENTER_SESSION_HOURS=8
```

El usuario inicial se crea solamente cuando no existe ningún usuario administrativo.
La contraseña no se almacena: se deriva mediante PBKDF2 con sal individual. Los tokens
de sesión se guardan como hashes y permanecen en `sessionStorage` del navegador.

No publique la consola hasta configurar credenciales propias. Si
`CONTROL_CENTER_AUTH_ENABLED=false`, las funciones anteriores quedan disponibles en
modo compatible, pero el módulo de gobierno permanece deshabilitado.
