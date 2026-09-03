# Arquitectura modular del Agent Service

La migración desde `main.py` es incremental para conservar las rutas públicas y
reducir el riesgo de regresiones.

## Capas

- `api/controllers/`: transporte HTTP. Valida parámetros, invoca servicios y
  traduce errores de aplicación a respuestas HTTP.
- `api/schemas/`: modelos Pydantic de entrada y salida. No contiene consultas ni
  reglas de negocio.
- `services/`: casos de uso y coordinación transaccional. No depende de FastAPI.
- `agent/`: razonamiento, prompts, autorización y ejecución del agente.
- `connectors/`: acceso a PostgreSQL, SQL Server y servicios externos.
- `historical/`: extracción e ingesta histórica por agente.
- `system/`: procesos de sincronización, escucha y aprendizaje continuo.
- `rag/`: almacenamiento y recuperación vectorial.
- `llm/`: proveedores y construcción de modelos.

## Regla de dependencias

`controller -> service -> domain/connector`

Un conector nunca debe importar un controlador. Los servicios no deben lanzar
`HTTPException`; exponen errores de aplicación para que el controlador decida el
código HTTP.

## Módulos HTTP

### `agent_prompts`

- Controlador: `api/controllers/agent_prompts.py`
- Contratos: `api/schemas/agent_prompts.py`
- Casos de uso: `services/agent_prompt_service.py`
- Persistencia: funciones de prompts en `connectors/db_client.py`

Gestiona generación individual, generación masiva idempotente y publicación de
plantillas versionadas.

## Próximas extracciones

1. `solidset_configuration`: instancias, recursos, canales y sincronización de
   ámbitos.
2. `historical_ingestion`: ejecución, cursores, auditoría y eliminación.
3. `agent_execution`: conversación síncrona y asíncrona.
4. `learning_feedback`: reacciones, refuerzo y conocimiento privado.
5. `llm_providers`: proveedores y modelos asignados.
6. `observability`: salud, métricas y diagnósticos.

Cada extracción debe conservar rutas, añadir pruebas del contrato OpenAPI y
evitar dependencias circulares con `main.py`.

## Dominios extraídos

- `agent_prompts`: generación individual, generación masiva y publicación.
- `ingestion`: ingesta histórica y conocimiento estructurado del sistema.
- `synchronization`: recursos, logins, canales y ámbitos SolidSET.
- `llm_configuration`: proveedores LLM y modelos asignados a agentes.
