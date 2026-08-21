Basándome en [historico-mensaje.md](D:\Trabajo\agente-robotea\historico-mensaje.md) y [propuesta-implementacion-historica.md](D:\Trabajo\agente-robotea\propuesta-implementacion-historica.md), propongo una ingesta retroactiva orientada a construir conocimiento verificable para cada agente, sin entrenar directamente los pesos del modelo.

## Objetivo

Transformar el histórico de mensajes de SolidSET en cuatro tipos de memoria:

```text
Mensajes SQL Server
        │
        ├── Memoria episódica: conversaciones y eventos
        ├── Memoria semántica: hechos y decisiones
        ├── Perfil del propietario: preferencias y forma de trabajar
        └── Conocimiento compartido: canales y contenido corporativo
```

El modelo consultaría estas memorias mediante RAG antes de responder.

## Principio fundamental

No se deben enviar millones de mensajes al modelo para que los “memorice”.

La estrategia correcta es:

```text
Extraer → limpiar → autorizar → clasificar → consolidar
→ generar embeddings → indexar → recuperar al responder
```

Esto permite:

- Actualizar conocimiento.
- Eliminar información.
- Corregir hechos.
- Conservar fuentes.
- Separar agentes.
- Evitar reentrenamientos completos.

## Capas de conocimiento

### 1. Mensajes históricos originales

Cada mensaje aceptado se guarda como unidad auditable.

Ejemplo:

```json
{
  "documentType": "historical_message",
  "text": "Alejandro Veitia: La entrega del proyecto será el viernes.",
  "metadata": {
    "idChat2": 1824911,
    "idSenderResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
    "idWorkRoom": "d8e82821-d52f-44bf-9b70-682651a6196e",
    "stamp": "2026-08-20T10:00:00Z",
    "scope": "owner",
    "source": "solidset_sql_history"
  }
}
```

Esta capa sirve para responder preguntas concretas y mostrar evidencia.

### 2. Episodios conversacionales

Los mensajes relacionados deben agruparse en ventanas coherentes:

```text
Mismo canal o meeting
+ mismos participantes
+ proximidad temporal
+ mismo tema
```

Ejemplo:

```json
{
  "documentType": "conversation_episode",
  "summary": "Alejandro y Víctor acordaron entregar el proyecto el viernes.",
  "sourceChatIds": [1824901, 1824907, 1824911],
  "participants": ["Alejandro Veitia", "Victor Vargas"]
}
```

Esto evita recuperar mensajes aislados sin contexto.

### 3. Hechos y decisiones

Un proceso de consolidación extraería conocimiento durable:

```json
{
  "documentType": "decision",
  "fact": "La entrega del proyecto SolidSET fue acordada para el viernes.",
  "confidence": 0.91,
  "sourceChatIds": [1824907, 1824911],
  "validFrom": "2026-08-20",
  "validTo": null,
  "version": 1
}
```

El modelo debería priorizar estos documentos sobre mensajes aislados.

### 4. Perfil del propietario

Cada agente aprendería de los mensajes escritos por su recurso humano:

```json
{
  "documentType": "owner_profile",
  "agentResourceId": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
  "preferences": [
    "Prefiere respuestas técnicas en español.",
    "Trabaja frecuentemente con SolidSET."
  ],
  "responsibilities": [
    "Integraciones entre SQL Server y PostgreSQL."
  ],
  "sourceChatIds": [1824001, 1824150, 1824911]
}
```

No se deben convertir automáticamente opiniones puntuales en preferencias permanentes. Una preferencia debería requerir repetición o confirmación explícita.

## Separación por scope

Cada documento debe pertenecer a un scope obligatorio.

### `owner`

Conocimiento privado del propietario:

```text
IDAgentResource
IDResource propietario
```

Solo lo consulta ese agente.

### `workroom`

Conocimiento compartido dentro de un canal:

```text
IDWorkRoom
agentes autorizados
```

Solo pueden consultarlo agentes asignados al canal.

### `meeting`

Conocimiento limitado a una reunión:

```text
IDMeeting
participantes autorizados
```

### `global`

Contenido corporativo explícitamente público:

```text
IDSolidSETInstance
scope=global
```

Nunca se debe promover automáticamente una conversación privada a conocimiento global.

## Flujo propuesto

### Etapa 1: descubrimiento

Confirmar en SQL Server:

- Tabla real de chat.
- Tabla de destinatarios.
- Mensajes eliminados.
- Visibilidad.
- Meetings.
- Mensajes generados por IA.
- Participantes y tipos.
- Índices disponibles.

### Etapa 2: extracción incremental

Leer lotes usando:

```sql
WHERE IDChat2 > @LastIDChat2
ORDER BY IDChat2
```

Configuración inicial:

```text
500 mensajes por lote
```

El productor no debe modificar todavía el cursor.

### Etapa 3: publicación en Redis

Stream independiente:

```text
machining:historical-ingestion:v1
```

Un lote contendría:

```json
{
  "batchId": "solidset-1:1824000:1824499",
  "instanceId": "solidset-1",
  "firstIdChat2": 1824000,
  "lastIdChat2": 1824499,
  "attempt": 0
}
```

Preferiblemente el Stream debería transportar referencias y no miles de textos completos. El worker puede volver a obtener el lote usando `batchId`.

### Etapa 4: normalización

Aplicar:

- Limpieza HTML.
- Normalización de espacios y Unicode.
- Eliminación de firmas repetitivas.
- Detección de idioma.
- Detección de secretos.
- Eliminación de eventos técnicos.
- Detección de respuestas IA.
- Cálculo de hash.

### Etapa 5: autorización

Antes de generar embeddings:

```text
¿Quién escribió el mensaje?
¿Quién podía verlo?
¿Era privado, de canal, meeting o global?
¿Qué agentes están autorizados?
```

Si no se puede determinar con seguridad, el mensaje debe quedar rechazado como:

```text
invalid_participants
```

No debe asumirse visibilidad.

### Etapa 6: clasificación semántica

Clasificar el mensaje como:

```text
question
statement
advice
command
decision
correction
preference
commitment
technical
social
unknown
```

Ejemplos:

```text
“La entrega será el viernes” → statement + possible_fact
“Confirmamos la entrega para el viernes” → decision
“Prefiero recibir informes en PDF” → preference
“No, la fecha correcta es el lunes” → correction
```

Esta clasificación determina si el mensaje debe:

- Indexarse únicamente como conversación.
- Convertirse en hecho.
- Actualizar una decisión.
- Modificar el perfil del propietario.
- Ser descartado.

### Etapa 7: construcción de documentos

Generar documentos deterministas:

```text
Mensaje:
solidset:{instance}:chat:{idChat2}

Owner:
solidset:{instance}:chat:{idChat2}:owner:{agent}

Workroom:
solidset:{instance}:chat:{idChat2}:workroom:{room}

Meeting:
solidset:{instance}:chat:{idChat2}:meeting:{meeting}

Resumen:
solidset:{instance}:summary:{scope}:{period}:{version}
```

### Etapa 8: embeddings e indexación

Guardar en Qdrant:

- Texto normalizado.
- Embedding.
- Scope.
- Agente autorizado.
- Canal o meeting.
- Fecha.
- Autor.
- `IDChat2`.
- Tipo documental.
- Confianza.
- Hash.
- Estado de vigencia.

PostgreSQL conservará la relación entre documento y punto Qdrant.

### Etapa 9: confirmación

Después de indexar completamente el lote:

1. Registrar auditoría.
2. Actualizar cursor.
3. Ejecutar `XACK`.

Si falla:

- No avanzar el cursor.
- Reintentar el lote.
- Los identificadores deterministas evitarán duplicados.

## Consolidación del conocimiento

La consolidación debería ejecutarse después de indexar suficientes mensajes.

Frecuencia propuesta:

```text
owner_profile: mensual
workroom_summary: semanal
meeting_summary: al finalizar meeting
decisions: continua
preferences: cuando exista evidencia suficiente
```

Reglas:

- Una decisión explícita puede consolidarse inmediatamente.
- Una preferencia necesita repetición o confirmación.
- Un hecho contradictorio crea una nueva versión.
- Los hechos temporales necesitan `validFrom` y `validTo`.
- Todo conocimiento consolidado conserva `sourceChatIds`.

## Resolución de contradicciones

Ejemplo:

```text
Mensaje antiguo: “La entrega será el viernes”.
Mensaje nuevo: “La entrega cambia al lunes”.
```

No deben coexistir como hechos igualmente válidos.

Resultado:

```json
{
  "fact": "La entrega será el lunes.",
  "version": 2,
  "supersedes": "decision-version-1",
  "sourceChatIds": [1825100],
  "validFrom": "2026-08-21"
}
```

El mensaje anterior permanece auditable, pero deja de ser la versión vigente.

## Recuperación al responder

El orden recomendado sería:

```text
1. Perfil del propietario
2. Mensajes recientes de la conversación
3. Decisiones vigentes
4. Conocimiento del workroom
5. Conocimiento del meeting
6. Conocimiento global
7. Histórico original como respaldo
```

Filtros obligatorios:

```text
IDSolidSETInstance
IDResource propietario
IDAgentResource
IDWorkRoom
IDMeeting
scope
authorized=true
deleted=false
```

## Evitar contaminación del modelo

No debe aprenderse como hecho:

- Una pregunta del usuario.
- Una posibilidad.
- Una broma.
- Una suposición.
- Una respuesta IA.
- Información negada posteriormente.
- Una opinión aislada.
- Contenido privado ajeno.

Ejemplo:

```text
“¿La reunión es mañana?”
```

No significa:

```text
“La reunión es mañana.”
```

Debe clasificarse como pregunta, no como hecho.

## Operación progresiva

### Modo `dry-run`

Primero ejecutar sin escribir Qdrant:

```text
Leer → normalizar → clasificar → auditar
```

Revisar:

- Mensajes aceptados.
- Mensajes rechazados.
- Scopes.
- Agentes autorizados.
- Secretos detectados.

### Piloto

```text
1 instancia
1 agente
2 canales
1.000 mensajes
```

### Expansión

```text
10.000 mensajes
→ 100.000 mensajes
→ histórico completo
```

Detener automáticamente si:

- Hay errores de permisos.
- Aumentan mensajes sin participantes.
- Qdrant rechaza documentos.
- El porcentaje de secretos supera el umbral.
- La ingesta afecta la latencia del agente.

## Métricas necesarias

```text
messages_read
messages_accepted
messages_rejected
messages_sensitive
messages_generated_by_ia
documents_indexed
documents_updated
documents_deleted
duplicate_documents
batches_pending
batches_failed
embedding_latency
retrieval_accuracy
privacy_violations
```

La métrica crítica debe ser:

```text
privacy_violations = 0
```

## Resultado esperado

El agente no “memorizará” literalmente toda la base. Dispondrá de un sistema de conocimiento retroactivo compuesto por:

- Mensajes originales auditables.
- Episodios conversacionales.
- Decisiones versionadas.
- Hechos verificables.
- Perfil privado del propietario.
- Resúmenes de canales y meetings.
- Conocimiento corporativo autorizado.

La propuesta permite aprender del pasado sin mezclar agentes, sin perder trazabilidad y sin convertir preguntas, rumores o mensajes generados por IA en hechos del sistema.