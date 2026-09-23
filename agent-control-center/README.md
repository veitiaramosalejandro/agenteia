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

La ingestión del catálogo requiere la clave configurada en
`HISTORICAL_INGESTION_ADMIN_KEY`. La consola la solicita al operador y la
mantiene solo en memoria; no se guarda en `localStorage` ni se envía al backend
del frontend.

Las credenciales de proveedores nunca se muestran. La autenticación administrativa y auditoría completa pertenecen a la fase de gobierno.
