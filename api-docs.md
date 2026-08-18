# API del agente SolidSET

Última actualización: 17 de agosto de 2026.

> Este documento debe actualizarse en el mismo cambio que modifique una ruta, método HTTP, contrato de entrada, respuesta o comportamiento observable de la API.

Actualmente la API expone 24 endpoints funcionales. Puedes consultar siempre la documentación interactiva en:

```text
http://localhost:8000/docs
```

## Flujo principal recomendado

El funcionamiento habitual sería:

```text
1. Sincronizar recursos desde SQL Server
2. Sincronizar las cuentas SysLogin
3. Sincronizar el catálogo SysWorkRoom
4. Sincronizar las relaciones recurso–canal
5. Configurar qué recursos son agentes IA
6. Activar agentes dentro de sus canales
7. Cargar conocimiento privado para cada agente
8. SolidSET selecciona uno o varios agentes
9. Ejecutar el diálogo multiagente
10. Cada agente responde con memoria y conocimiento independientes
```

# Configuración multiagente

## 1. Guardar o actualizar un agente

```http
POST /api/v1/agent/solidset/chat-configuration
```

Crea o actualiza un agente utilizando `IDResource` como identificador único.

```json
{
  "Name": "Agente de mantenimiento",
  "Stamp": "2026-08-17T16:30:00",
  "IDResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
  "active": true
}
```

Comportamiento:

- Si el recurso no existe, crea `SysResourceIA`.
- Si ya existe, actualiza nombre, fecha y estado.
- `active = false` impide que el agente responda.
- El campo `ID` interno lo genera PostgreSQL automáticamente.

---

## 2. Configurar un agente dentro de un canal

```http
PUT /api/v1/agent/solidset/agents/{agent_resource_id}/workrooms/{workroom_id}
```

Activa, desactiva u ordena al agente dentro de un canal específico.

```json
{
  "active": true,
  "response_order": 1
}
```

Ejemplo:

```http
PUT /api/v1/agent/solidset/agents/ce0e837a-fe28-47ae-9ba0-8841fe042ca8/workrooms/007e3b2a-bbf6-4f46-8cbd-26d26db06ec1
```

Comportamiento:

- Hace `UPSERT` en `SysChatIAResource`.
- `active` controla si puede responder en ese canal.
- `response_order` determina su posición frente a otros agentes.
- Un agente puede estar activo globalmente y desactivado solamente en un canal.

---

## 3. Añadir conocimiento propio a un agente

```http
POST /api/v1/agent/solidset/agents/{agent_resource_id}/knowledge
```

Guarda conocimiento privado en PostgreSQL y lo indexa en Qdrant.

Conocimiento general del agente:

```json
{
  "Title": "Especialización",
  "KnowledgeText": "Este agente está especializado en mantenimiento preventivo de tornos CNC.",
  "Source": "solidset",
  "active": true
}
```

Conocimiento específico de un canal:

```json
{
  "IDWorkRoom": "007e3b2a-bbf6-4f46-8cbd-26d26db06ec1",
  "Title": "Máquinas del canal",
  "KnowledgeText": "Este canal gestiona los tornos de la línea cuatro.",
  "Source": "solidset",
  "active": true
}
```

Comportamiento:

- Guarda el contenido en `SysResourceIAKnowledge`.
- Si no contiene `IDWorkRoom`, puede utilizarlo el agente en todos sus canales.
- Si contiene `IDWorkRoom`, solo se utiliza dentro de ese canal.
- Otro agente no puede recuperar este conocimiento.
- `active = false` conserva el contenido, pero deja de utilizarlo.

---

## 4. Ejecutar varios agentes

```http
POST /api/v1/agent/solidset/multi-agent/dialogue
```

Es el endpoint principal de la arquitectura multiagente.

```json
{
  "IDWorkRoom": "007e3b2a-bbf6-4f46-8cbd-26d26db06ec1",
  "IDSession": "06e64429-fb46-4544-a0be-c6bbde4acd66",
  "RawMessage": "¿Cuál puede ser la causa de esta alarma?",
  "SenderResourceId": "ba55b081-3e30-4f38-9816-194720c6701f",
  "SelectedAgentResourceIds": [
    "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
    "272700d8-d1ba-46a6-a121-b76fce8ecb9f"
  ],
  "SendToSolidSET": false
}
```

Comportamiento:

1. Comprueba que cada agente existe.
2. Comprueba que está activo globalmente.
3. Comprueba que está activo y asignado al canal.
4. Crea o actualiza una fila en `SysAgentIASession`.
5. Ejecuta todos los agentes en paralelo.
6. Cada uno utiliza su propia memoria y conocimiento.
7. Devuelve una respuesta independiente por agente.
8. Con `SendToSolidSET = true`, publica las respuestas en el canal.

El mismo `IDResource` puede aparecer como remitente y agente configurado. Esto es válido cuando una persona interviene usando el recurso que SolidSET ha configurado como agente; el bloqueo de bucles se realiza mediante `Info.generated_by_ia`, no descartando el recurso remitente.

Respuesta:

```json
{
  "IDSession": "06e64429-fb46-4544-a0be-c6bbde4acd66",
  "IDWorkRoom": "007e3b2a-bbf6-4f46-8cbd-26d26db06ec1",
  "responses": [
    {
      "IDAgentResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
      "AgentName": "Agente de mantenimiento",
      "response": "La alarma puede estar relacionada con...",
      "sent": false,
      "sendDetail": null
    }
  ]
}
```

`IDSession` debe ser UUID. Si no se envía, la API genera uno.

# Sincronización con SQL Server

## 5. Sincronizar recursos

```http
POST /api/v1/agent/solidset/resources/sync
```

Ejecuta la consulta de `SysResources` y `SysLogin`.

Mapeo:

```text
SysResources.DisplayName → SysResourceIA.Name
SysResources.ResourceId  → SysResourceIA.IDResource
SysResources.ActiveIDLogin2Resource → SysResourceIA.ActiveIDLogin2Resource
```

La sincronización es idempotente:

- Inserta recursos nuevos.
- Actualiza recursos existentes.
- No crea duplicados.
- Conserva el estado `active`.

Respuesta aproximada:

```json
{
  "status": "synchronized",
  "sourceRows": 126,
  "synchronized": 126,
  "inserted": 0,
  "updated": 126,
  "skipped": 0
}
```

---

## 6. Sincronizar recursos y canales

```http
POST /api/v1/agent/solidset/chat-workroom/sync
```

Sincroniza las asignaciones de recursos a canales desde:

```text
SysResources
SysLogin
SysWorkRoomResource
SysWorkRoom
```

Mapeo:

```text
ResourceId → SysChatIAResource.IDResource
IDWorkRoom → SysChatIAResource.IDWorkRoom
```

Comportamiento:

- Crea las relaciones recurso–canal.
- Evita duplicados por `(IDResource, IDWorkRoom)`.
- No modifica las sesiones.
- Las relaciones nuevas quedan activas por defecto.

---

## 7. Sincronizar el catálogo de canales

```http
POST /api/v1/agent/solidset/workrooms/sync
```

Ejecuta esta consulta en SQL Server:

```sql
SELECT Code, Name, Description, IDWorkRoom
FROM dbo.SysWorkRoom;
```

Mapeo hacia PostgreSQL:

```text
Code        → SysWorkRoom.Code
Name        → SysWorkRoom.Name
Description → SysWorkRoom.Description
IDWorkRoom  → SysWorkRoom.IDWorkRoom
```

La sincronización hace `UPSERT` por `IDWorkRoom`, elimina los espacios añadidos por el tipo `NCHAR` de SQL Server y no crea duplicados.

`SysWorkRoom.IDWorkRoom` es la clave padre de:

- `SysChatIAResource.IDWorkRoom`.
- `SysResourceIAKnowledge.IDWorkRoom`.
- `SysAgentIASession.IDWorkRoom`.

Respuesta aproximada:

```json
{
  "status": "synchronized",
  "sourceRows": 29976,
  "synchronized": 29976,
  "inserted": 0,
  "updated": 29976,
  "skipped": 0
}
```

---

## 8. Sincronizar cuentas de acceso

```http
POST /api/v1/agent/solidset/logins/sync
```

Ejecuta en SQL Server:

```sql
SELECT Username, FullName, Password, Salt, IDLogin,
       LastIDResource, ActiveIDLogin2Resource
FROM dbo.SysLogin;
```

Guarda los datos en PostgreSQL `SysLogin` mediante un `UPSERT` por `IDLogin`. La cuenta exacta del agente se resuelve uniendo `SysResourceIA.ActiveIDLogin2Resource` con `SysLogin.ActiveIDLogin2Resource`; esto evita elegir otro usuario que tenga el mismo `LastIDResource`.

Mapeo adicional:

```text
dbo.SysLogin.FullName → PostgreSQL SysLogin.FullName
```

Al enviar una respuesta automática o multiagente, el router entrega el `agent_resource_id` seleccionado al método `_solidset_login`. Este método busca una cuenta cuyo `SysLogin.LastIDResource` coincida con `SysResourceIA.IDResource` y, antes de autenticar, exige que `SysResourceIA.active=true`. Después inicia una sesión independiente con `POST /User/LoginJson` y publica el mensaje con las cookies de esa misma sesión.

La autenticación del agente envía internamente:

```text
UserName          = SysLogin.Username
Password          = SysLogin.Password
PasswordEncrypted = true
TimezoneID        = SOLIDSET_TIMEZONE_ID
Resources[0]      = SysResourceIA.IDResource
```

`SysLogin.Password` es el HMAC ya generado por SolidSET, no una contraseña reversible. `PasswordEncrypted=true` hace que el método de SolidSET omita `GenerateHMAC` y compare directamente ese valor. `Resources[0]` obliga a registrar la sesión con el recurso agente solicitado cuando el login dispone de varios recursos. Si el recurso no es un agente activo, no tiene una cuenta válida o `LoginJson` rechaza el acceso, el envío falla explícitamente y no utiliza la identidad global configurada en `.env`.

La respuesta publicada usa `SysLogin.FullName` y conserva una identificación visible con el formato `Asistente IA {FullName}: respuesta`; por ejemplo, `Asistente IA Alejandro Veitia: ...`. SolidSET muestra además como emisor el login propio del recurso. Si excepcionalmente `FullName` está vacío, se utiliza `SysResourceIA.Name` como respaldo.

La respuesta contiene únicamente contadores; nunca devuelve ni registra `Password` o `Salt`:

```json
{
  "status": "synchronized",
  "sourceRows": 325,
  "synchronized": 325,
  "inserted": 325,
  "updated": 0,
  "skipped": 0
}
```

Los campos `Password` y `Salt` son datos sensibles. El acceso al esquema PostgreSQL debe quedar limitado al servicio del agente y no deben incluirse en endpoints de consulta, logs ni mensajes de error.

# Entrada de mensajes desde SolidSET

## 9. Recibir una notificación FrameworkMessage

```http
POST /api/v1/agent/notification/framework-message
```

Recibe directamente un `FrameworkMessage` de SolidSET.

Funciones:

- Normaliza el mensaje.
- Lo captura para aprendizaje.
- Lo indexa en Qdrant.
- Identifica los agentes seleccionados.
- Programa respuestas automáticas si corresponde.
- Descarta mensajes marcados como `generated_by_ia`.

Este endpoint no funciona como proxy: recibe y procesa el mensaje.

Para identificar agentes candidatos, el router admite estas fuentes del payload:

```text
Destiny.dests[].resource
SelectedAgentResourceIds[] (solo cuando Destiny.dests está vacío)
Destiny.resource (solo cuando Destiny.dests está vacío)
```

En mensajes dirigidos, `Destiny.dests[].resource` es la fuente de verdad y tiene precedencia absoluta. Solo responde el recurso destinatario si existe en `SysResourceIA`, tiene `active=true` y está habilitado para el canal. Una lista auxiliar `SelectedAgentResourceIds` no puede añadir otros agentes cuando `Destiny.dests` contiene destinatarios.

`Chat.resourceTable` y `Chat.destiny` describen participantes del chat y nunca se usan para seleccionar agentes. De este modo, estar presente en el canal no autoriza a un agente a responder. Si el recurso destinatario activo todavía no tiene relación con un canal privado o dinámico, el router crea exclusivamente para ese destino `SysChatIAResource(IDResource, IDWorkRoom)` con `active=true`.

`Chat.channels[].idChannel` y `Chat.idWorkRoom` se interpretan como `SysWorkRoom.IDWorkRoom`.

Un mensaje humano puede tener el mismo `Sender.resource` que el agente configurado. El agente puede responder porque SolidSET utiliza ese recurso como identidad compartida; únicamente se descartan mensajes que lleguen marcados con `Info.generated_by_ia`.

### Respuestas dentro de meetings

Cuando el mensaje contiene `Info.meeting_id`, `ExtraData.meeting_id` o `Chat.idMeeting`, la respuesta se mantiene dentro del meeting. `meeting_mirror_general` ya no es necesario para detectar este contexto.

El formulario enviado a `/Chat/SendMessageForm` incluye:

```text
Destiny.WorkRoom      = canal técnico subyacente
Info[meeting_id]      = UUID del meeting
Info[meeting_code]    = código opcional, por ejemplo M10
ExtraData             = {"meeting_id":"...","meeting_code":"M10"}
```

El `WorkRoom` se conserva únicamente porque SolidSET lo utiliza como ruta de transporte. `ExtraData.meeting_id` es lo que vincula el nuevo chat al meeting y activa las validaciones de participante bloqueado o expulsado mostradas por `MeetingChatSendGuard`. La API no añade `Info[meeting_mirror_general]`, evitando convertir la respuesta en un espejo general del canal.

En meetings, `Chat.destiny` es la fuente canónica para decidir qué agente responde:

```text
Chat.destiny[].type = 1 → autor de la pregunta; nunca responde
Chat.destiny[].type = 2 → destinatario solicitado; puede responder
Chat.destiny[].sequence → orden de los destinatarios
```

Cuando `Chat.destiny` está presente, el router ignora `Destiny.dests`, porque esta última colección puede contener copias técnicas para el autor y otros participantes del meeting. Solo los recursos `type=2` pasan después por las validaciones de `SysResourceIA.active` y asignación al canal técnico. Si únicamente existe una entrada `type=1`, no se ejecuta ningún agente. `Destiny.dests` se utiliza como respaldo exclusivamente si el payload de meeting no contiene `Chat.destiny`.

---

## 10. Capturar y reenviar FrameworkHub

```http
POST /api/v1/agent/notification/frameworkHub/SendMessage
```

Funciona como proxy entre SolidSET y el endpoint real de notificaciones.

Flujo:

```text
Mensaje entrante
    ↓
Captura e indexación
    ↓
Reenvío al endpoint real de SolidSET
    ↓
Programación de agentes seleccionados
```

Conserva el cuerpo, las cabeceras y los parámetros relevantes.

La respuesta incluye cabeceras como:

```text
X-Agent-Capture-Learned
X-Agent-Replies-Scheduled
```

# Conversación tradicional

## 11. Diálogo con un único agente

```http
POST /api/v1/agent/dialogue
```

Procesa un `FrameworkMessage` mediante el agente tradicional.

Utiliza principalmente:

```json
{
  "RawMessage": "Consulta del usuario",
  "Sender": {
    "resource": "UUID-del-usuario",
    "login": "UUID-del-login"
  },
  "Destiny": {
    "workRoom": "UUID-del-canal"
  }
}
```

Funciones:

- Valida contenido y longitud.
- Detecta prompt injection.
- Resuelve usuario, recurso y canal.
- Recupera contexto SQL Server y Qdrant.
- Usa memoria Redis.
- Ejecuta herramientas permitidas.
- Devuelve una única respuesta.

Para nuevos desarrollos con varios agentes debe preferirse `/multi-agent/dialogue`.

---

## 12. Registrar feedback

```http
POST /api/v1/agent/feedback
```

Registra una valoración, corrección o señal de aprendizaje.

```json
{
  "session_id": "session-123",
  "user_id": "usuario",
  "user_text": "La pregunta original",
  "agent_response": "La respuesta del agente",
  "corrected_response": "La respuesta correcta",
  "canal_id": "UUID-del-canal",
  "feedback_type": "explicit",
  "reason": "La causa indicada era incorrecta",
  "update_profile": true
}
```

Funciones:

- Analiza la reacción.
- Guarda la corrección.
- Actualiza el perfil dinámico.
- Incorpora aprendizaje de largo plazo.

Para feedback multiagente convendría que SolidSET conserve también el `IDAgentResource` que produjo la respuesta.

---

## 13. Capturar una reacción de SolidSET

```http
POST /api/v1/agent/solidset/reactions/capture
```

Recibe el mismo contrato de `ChangeReactionRequest` después de que SolidSET haya establecido `IDUser` desde la sesión:

```json
{
  "IDChat": 1822812,
  "IDUser": "1790fc78-023d-4506-a7e8-5c030e9386d1",
  "IDChannel": "d8e82821-d52f-44bf-9b70-682651a6196e",
  "IDEmoji": "U+1F64F",
  "Counter": 1
}
```

La API consulta `dbo.SysChat.IDChat2`, comprueba que el emisor esté registrado en `SysResourceIA` y que el mensaje comience con `Asistente IA`. Después guarda el evento en PostgreSQL `SysAgentIAReaction` y lo incorpora al aprendizaje aislado del agente que emitió la respuesta.

Señales posibles:

```text
positive → aprobación, agradecimiento, corazón, celebración
negative → desaprobación, enfado o tristeza
neutral  → emoji sin clasificación explícita
removed  → Counter = 0; se registra la retirada pero no se aprende
```

La combinación `(IDChat, IDUser, IDEmoji)` es idempotente. Repetir el mismo contador no genera aprendizaje duplicado.

Respuesta:

```json
{
  "status": "captured",
  "learned": true,
  "changed": true,
  "signal": "positive",
  "IDChat": 1822812,
  "IDAgentResource": "272700d8-d1ba-46a6-a121-b76fce8ecb9f",
  "AgentName": "Asistente IA Victor Vargas"
}
```

# Memoria y archivos

## 14. Consultar historial

```http
GET /api/v1/agent/history/{session_id}
```

Devuelve mensajes almacenados en Redis.

Parámetros opcionales:

```text
before
limit
```

Se utiliza para paginar el historial de una conversación.

En multiagente, el identificador interno incluye agente, canal y sesión.

---

## 15. Eliminar historial

```http
DELETE /api/v1/agent/history/{session_id}
```

Borra la memoria Redis correspondiente a una sesión.

Es una operación destructiva: elimina el historial conversacional de esa clave.

---

## 16. Obtener audio generado

```http
GET /api/v1/agent/audio-response?file=nombre.mp3
```

Devuelve un archivo de audio creado previamente por el agente.

Valida que el archivo exista y que la ruta solicitada sea segura.

# Supervisión y diagnóstico

## 17. Estado general del agente

```http
GET /api/v1/agent/health
```

Comprueba el estado del servicio y dependencias como:

- Ollama.
- Qdrant.
- Redis.
- PostgreSQL.
- SQL Server.
- SolidSET.
- Notification API.

Es el endpoint principal para monitorización.

---

## 18. Resumen de evaluación

```http
GET /api/v1/agent/evaluation/summary
```

Devuelve métricas operativas relacionadas con:

- Diálogos procesados.
- Duración.
- Caché.
- Errores.
- Captura de notificaciones.
- Autorrespuestas.
- Estado de integraciones.

---

## 19. Mensajes recientes capturados

```http
GET /api/v1/agent/notification/recent-messages?limit=30
```

Devuelve los últimos mensajes capturados por el listener.

El límite permitido está entre 1 y 200.

Sirve para comprobar que SolidSET está enviando correctamente:

- Mensaje.
- Canal.
- Remitente.
- Identificadores.
- Tipo de evento.

---

## 20. Contexto de un usuario

```http
GET /api/v1/agent/context/{user_id}
```

Devuelve el contexto calculado para un usuario:

- Identidad.
- Roles.
- Canales.
- Actividades recientes.
- Recursos disponibles.
- Permisos.
- Perfil aprendido.

Se usa principalmente para depuración.

---

## 21. Métricas de reintentos SQL

```http
GET /api/v1/agent/sql-retry-stats
```

Muestra:

- Reintentos de conexión.
- Reintentos de consultas.
- Operaciones que generaron reintentos.
- Última fecha de reintento.

---

## 22. Reiniciar métricas SQL

```http
POST /api/v1/agent/sql-retry-stats/reset
```

Pone a cero las métricas de reintentos SQL.

No modifica tablas ni datos de SolidSET; solamente reinicia contadores internos.

# Conectividad

## 23. Probar SolidSET

```http
GET /api/v1/connectivity/solidset
```

Prueba la conectividad con SolidSET, normalmente mediante su endpoint de heartbeat.

Sirve para diagnosticar:

- URL incorrecta.
- Servicio no disponible.
- Timeout.
- Problemas TLS.
- Respuesta HTTP inesperada.

---

## 24. Probar todas las integraciones

```http
GET /api/v1/connectivity/all
```

Ejecuta una comprobación conjunta de los servicios configurados.

Permite localizar rápidamente si el problema está en:

- SolidSET.
- Notification API.
- PostgreSQL.
- SQL Server.
- Redis.
- Qdrant.
- Ollama.

## Tablas y responsabilidades

```text
SysResourceIA ──────────────┐
Identidad del agente        │
                            ├──► SysChatIAResource
SysWorkRoom ────────────────┘    Asignación agente–canal
Catálogo de canales                  │
                                     ├── active
                                     └── response_order
                                     │
                                     ▼
SysAgentIASession
    Conversaciones por agente y canal

SysResourceIAKnowledge
    Conocimiento privado por agente y, opcionalmente, canal
        │
        ▼
Qdrant
    Recuperación semántica aislada

SysAgentIASession
        │
        ▼
Redis
    Memoria conversacional rápida
```

La regla central es:

```text
Un mensaje puede seleccionar varios agentes.
Cada agente se valida y ejecuta por separado.
Cada agente mantiene su propia sesión, memoria y conocimiento.
```
