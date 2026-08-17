# API del agente SolidSET

Última actualización: 17 de agosto de 2026.

> Este documento debe actualizarse en el mismo cambio que modifique una ruta, método HTTP, contrato de entrada, respuesta o comportamiento observable de la API.

Actualmente la API expone 23 endpoints funcionales. Puedes consultar siempre la documentación interactiva en:

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
SELECT Username, Password, Salt, IDLogin,
       LastIDResource, ActiveIDLogin2Resource
FROM dbo.SysLogin;
```

Guarda los datos en PostgreSQL `SysLogin` mediante un `UPSERT` por `IDLogin`. `LastIDResource` permite localizar la cuenta correspondiente al `IDResource` de un agente activo.

Al enviar una respuesta automática o multiagente, la API usa el `agent_resource_id` para buscar una cuenta cuyo `SysLogin.LastIDResource` coincida con `SysResourceIA.IDResource`. Solo se admite si el agente continúa con `active=true`. La API inicia una sesión independiente con `POST /User/LoginJson`, enviando `UserName`, `Password` y `TimezoneID`, y después publica el mensaje con las cookies de esa misma sesión. Si no existe una cuenta válida o `LoginJson` rechaza el acceso, el envío falla explícitamente y no utiliza la identidad global configurada en `.env`.

La respuesta publicada conserva una identificación visible con el formato `Nombre del agente: respuesta`. Por tanto, SolidSET muestra como emisor el login propio del recurso y el contenido deja claro qué agente IA produjo la contestación.

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
SelectedAgentResourceIds[]
Destiny.dests[].resource
Chat.resourceTable[].idResource
Chat.destiny[].idResource
```

`Chat.channels[].idChannel` y `Chat.idWorkRoom` se interpretan como `SysWorkRoom.IDWorkRoom`. Los recursos encontrados todavía deben tener `SysResourceIA.active = true` y una relación activa en `SysChatIAResource`; por eso los usuarios normales incluidos en `resourceTable` no se ejecutan como agentes.

Cuando un recurso activo aparece en `Chat.resourceTable` o `Chat.destiny` y todavía no tiene relación con el canal, el router crea automáticamente `SysChatIAResource(IDResource, IDWorkRoom)` con `active = true`. Esto cubre canales privados o dinámicos descubiertos por primera vez mediante una notificación. Las selecciones enviadas únicamente mediante `SelectedAgentResourceIds` no crean relaciones implícitas.

Un mensaje humano puede tener el mismo `Sender.resource` que el agente configurado. El agente puede responder porque SolidSET utiliza ese recurso como identidad compartida; únicamente se descartan mensajes que lleguen marcados con `Info.generated_by_ia`.

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

# Memoria y archivos

## 13. Consultar historial

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

## 14. Eliminar historial

```http
DELETE /api/v1/agent/history/{session_id}
```

Borra la memoria Redis correspondiente a una sesión.

Es una operación destructiva: elimina el historial conversacional de esa clave.

---

## 15. Obtener audio generado

```http
GET /api/v1/agent/audio-response?file=nombre.mp3
```

Devuelve un archivo de audio creado previamente por el agente.

Valida que el archivo exista y que la ruta solicitada sea segura.

# Supervisión y diagnóstico

## 16. Estado general del agente

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

## 17. Resumen de evaluación

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

## 18. Mensajes recientes capturados

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

## 19. Contexto de un usuario

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

## 20. Métricas de reintentos SQL

```http
GET /api/v1/agent/sql-retry-stats
```

Muestra:

- Reintentos de conexión.
- Reintentos de consultas.
- Operaciones que generaron reintentos.
- Última fecha de reintento.

---

## 21. Reiniciar métricas SQL

```http
POST /api/v1/agent/sql-retry-stats/reset
```

Pone a cero las métricas de reintentos SQL.

No modifica tablas ni datos de SolidSET; solamente reinicia contadores internos.

# Conectividad

## 22. Probar SolidSET

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

## 23. Probar todas las integraciones

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
