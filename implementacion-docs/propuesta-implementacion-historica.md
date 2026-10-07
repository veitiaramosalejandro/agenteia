Sí. Este sería el plan de implementación ejecutable basado en `historico-mensaje.md`, sin realizar todavía cambios.

## Objetivo

Ingerir retroactivamente los mensajes de SolidSET desde SQL Server para que cada agente disponga de:

- Conocimiento privado aprendido de su recurso humano.
- Conocimiento de los canales autorizados.
- Conocimiento corporativo global permitido.
- Trazabilidad completa hasta `IDChat2`.
- Ingesta incremental sin duplicados.
- Posibilidad de borrar o reconstruir conocimiento.

## Fase 1: descubrir el esquema SQL Server

Crear:

```text
scripts/sqlserver/discover_solidset_chat_schema.sql
```

Debe identificar:

- Tabla real de mensajes.
- Tabla de destinatarios.
- Relaciones con `SysLogin`, `SysResources` y `SysWorkRoom`.
- Campos `IDChat2`, `IDMeeting`, `Status`, `Kind`, visibilidad y eliminación.
- Cómo se identifica un mensaje generado por IA.
- Índices disponibles para hacer una extracción eficiente.

Resultado esperado: documento con tablas, columnas, claves y consultas definitivas.

Bloqueante: no avanzar a la extracción hasta confirmar la tabla de destinatarios y las reglas de privacidad.

## Fase 2: crear control de ingesta en PostgreSQL

Crear una migración:

```text
database/init/016_create_historical_ingestion.sql
```

Tablas:

### `SysAgentIAIngestionCursor`

Mantiene el último mensaje procesado por instancia:

```text
ID
IDSolidSETInstance
Source
LastIDChat2
LastStamp
LastRunAt
Status
Error
```

### `SysAgentIAIngestionAudit`

Registra cada lote:

```text
BatchID
FirstIDChat2
LastIDChat2
ReadCount
AcceptedCount
RejectedCount
IndexedCount
Status
Error
StartedAt
CompletedAt
```

### `SysAgentIAHistoricalDocument`

Relaciona PostgreSQL con Qdrant:

```text
DocumentID
IDChat2
IDSolidSETInstance
Scope
IDResource
IDAgentResource
IDWorkRoom
QdrantPointID
ContentHash
Status
IndexedAt
DeletedAt
```

## Fase 3: preparar consultas históricas

Crear:

```text
scripts/sqlserver/extract_historical_messages.sql
scripts/sqlserver/extract_message_participants.sql
scripts/sqlserver/extract_resource_workrooms.sql
```

Características:

- Lectura incremental por `IDChat2`.
- Orden ascendente.
- Lotes configurables.
- Relaciones con autor, login, recurso, canal y meeting.
- Obtención de todos los participantes.
- Filtros iniciales de mensajes vacíos y eliminados.

Lote inicial:

```text
500 mensajes
```

## Fase 4: normalización y privacidad

Crear un módulo conceptual:

```text
agent-service/app/historical/normalizer.py
```

Responsabilidades:

- Limpiar HTML.
- Normalizar Unicode.
- Eliminar contenido vacío.
- Detectar secretos y datos sensibles.
- Detectar mensajes generados por IA.
- Clasificar eventos técnicos.
- Calcular `contentHash`.
- Determinar si el mensaje puede aprenderse.

Motivos de rechazo:

```text
empty
deleted
generated_by_ia
technical
duplicate
unauthorized
sensitive
invalid_participants
```

Cada rechazo debe registrarse en la auditoría.

## Fase 5: resolver permisos y scopes

Crear:

```text
agent-service/app/historical/scope_resolver.py
```

Reglas:

### `owner`

Cuando el autor es el recurso humano propietario del agente:

```text
scope=owner
IDResource=propietario
```

### `workroom`

Cuando el agente está asignado al canal mediante `SysChatIAResource`:

```text
scope=workroom
IDResource=propietario
IDWorkRoom=canal
```

### `global`

Solamente para mensajes explícitamente públicos y autorizados:

```text
scope=global
IDSolidSETInstance=instancia
```

Regla crítica: un mensaje privado nunca puede indexarse para un agente ajeno.

## Fase 6: crear la cola histórica

Utilizar un Redis Stream independiente:

```text
machining:historical-ingestion:v1
```

No debe reutilizarse el Stream de respuestas en tiempo real.

El productor publicará:

```json
{
  "batchId": "instance:1824000:1824499",
  "instanceId": "...",
  "firstIdChat2": 1824000,
  "lastIdChat2": 1824499,
  "attempt": 0,
  "messages": []
}
```

Configuración propuesta:

```env
HISTORICAL_INGESTION_ENABLED=false
HISTORICAL_INGESTION_BATCH_SIZE=500
HISTORICAL_INGESTION_STREAM=machining:historical-ingestion:v1
HISTORICAL_INGESTION_GROUP=historical-workers-v1
HISTORICAL_INGESTION_MAX_RETRIES=3
```

Debe comenzar desactivado hasta finalizar la prueba piloto.

## Fase 7: implementar productor y worker

Crear:

```text
agent-service/app/historical/producer.py
agent-service/app/historical/worker.py
```

### Productor

1. Lee el cursor de PostgreSQL.
2. Consulta SQL Server.
3. Crea el lote.
4. Publica en Redis.
5. No avanza todavía el cursor.

### Worker

1. Consume el lote.
2. Normaliza mensajes.
3. Aplica privacidad.
4. Resuelve scopes.
5. Genera embeddings.
6. Guarda documentos en Qdrant.
7. Registra trazabilidad.
8. Actualiza la auditoría.
9. Avanza el cursor.
10. Ejecuta `XACK`.

El cursor solo avanza cuando todo el lote termina correctamente.

## Fase 8: indexación en Qdrant

Usar identificadores deterministas:

```text
solidset:{instance}:chat:{idChat2}:scope:{scope}:agent:{resource}
```

Metadatos:

```json
{
  "idChat2": 1824911,
  "solidsetInstanceId": "...",
  "idSenderResource": "...",
  "idWorkRoom": "...",
  "idMeeting": "...",
  "scope": "owner",
  "agentResourceId": "...",
  "stamp": "...",
  "contentHash": "...",
  "source": "solidset_sql_history"
}
```

Esto permitirá reintentos sin duplicidad.

## Fase 9: adaptar la recuperación del agente

El RAG deberá aplicar filtros obligatorios:

```text
IDSolidSETInstance
IDResource del agente
IDWorkRoom actual
scope permitido
```

Orden de recuperación:

1. `owner`
2. `workroom`
3. `global`

Nunca se debe recuperar conocimiento privado perteneciente a otro agente.

## Fase 10: consolidación periódica

Crear:

```text
agent-service/app/historical/consolidator.py
```

Agrupar mensajes por:

- Recurso y mes.
- Canal y semana.
- Meeting.
- Proyecto o tema.

Generar:

```text
owner_profile
workroom_summary
meeting_summary
decisions
preferences
responsibilities
frequent_topics
```

Cada hecho consolidado debe conservar:

```text
sourceChatIds
confidence
version
createdAt
```

## Fase 11: endpoints administrativos

Propuesta:

```http
POST /api/v1/agent/historical-ingestion/start
POST /api/v1/agent/historical-ingestion/pause
POST /api/v1/agent/historical-ingestion/retry/{batchId}
GET  /api/v1/agent/historical-ingestion/status
GET  /api/v1/agent/historical-ingestion/batches
GET  /api/v1/agent/historical-ingestion/cursor
DELETE /api/v1/agent/historical-ingestion/messages/{idChat2}
```

Estos endpoints deben estar protegidos como operaciones administrativas.

## Fase 12: despliegue

Añadir un servicio escalable:

```yaml
historical-worker:
  command: ["python", "-m", "app.historical.worker"]
```

Escalado:

```powershell
docker compose -f docker-compose-prod.yml up -d `
  --scale historical-worker=2
```

Debe limitarse su consumo de CPU/GPU para no afectar las respuestas en tiempo real.

## Prueba piloto

Alcance:

```text
1 instancia
1 agente
2 canales
1.000 mensajes
```

Criterios de aceptación:

- Cero duplicados.
- Cero mensajes privados ajenos.
- Cero respuestas IA aprendidas como conocimiento humano.
- Reanudación correcta después de detener el worker.
- Cursor actualizado únicamente tras confirmar el lote.
- Recuperación correcta por `IDChat2`.
- Eliminación sincronizada entre PostgreSQL y Qdrant.
- Sin impacto apreciable en respuestas en tiempo real.

## Orden de implementación

1. Descubrimiento SQL Server.
2. Validación de privacidad.
3. Migraciones PostgreSQL.
4. Consultas incrementales.
5. Normalizador.
6. Resolución de scopes.
7. Productor Redis.
8. Worker histórico.
9. Indexación Qdrant.
10. Filtros RAG.
11. Endpoints administrativos.
12. Consolidación.
13. Prueba piloto.
14. Activación progresiva.

El primer entregable debe ser `discover_solidset_chat_schema.sql`. Su resultado permitirá sustituir todas las tablas y campos conceptuales del documento por el esquema real de SolidSET.