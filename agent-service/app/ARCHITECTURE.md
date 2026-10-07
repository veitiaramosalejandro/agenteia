# Arquitectura modular del Agent Service

`main.py` es únicamente la raíz de composición ASGI: crea FastAPI, registra
middleware, lifecycle, manejadores de error y routers. No contiene contratos ni
lógica de endpoints.

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

## Dominios extraídos

- `agent_prompts`: generación individual, generación masiva y publicación.
- `ingestion`: ingesta histórica y conocimiento estructurado del sistema.
- `synchronization`: recursos, logins, canales y ámbitos SolidSET.
- `llm_configuration`: proveedores LLM y modelos asignados a agentes.
- `solidset_instances`: configuración, conexión y catálogo SQL por instancia.
- `agent_management`: conocimiento privado, asignación a canales y diálogo
  multiagente.
- `conversation`: diálogo directo y validaciones de entrada.
- `notifications`: recepción, vista previa y proxy de FrameworkMessage.
- `suggestions`: aceptación asíncrona del pedido de sugerencias.
- `responses`: estado y métricas de las colas asíncronas.
- `feedback`: feedback explícito y reacciones SolidSET.
- `history`: audio e historial conversacional.
- `diagnostics`: salud, observabilidad y conectividad.

## Servicios de ejecución

- `container.py`: crea una sola vez agente, orquestador, listener y colas para
  HTTP y workers; los workers ya no importan `main.py`.
- `services/auto_reply.py`: autorización, selección y respuesta automática por
  agente.
- `services/suggestions.py`: contexto acotado, verificación, búsqueda y
  generación de sugerencias.
- `services/response_status.py`: estado durable/fallback de respuestas.
- `services/dialogue_runtime.py`: admisión, concurrencia, caché y métricas.
- `services/instance_resolution.py`: resolución tenant-safe de la instancia.
- `services/connectivity.py`: pruebas de dependencias externas.

El contrato HTTP se verifica mediante OpenAPI: deben existir exactamente las
50 rutas históricas, sin duplicados.
