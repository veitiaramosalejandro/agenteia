La arquitectura puede evolucionar de un único agente global a varios agentes independientes, donde cada recurso configurado en SolidSET representa una identidad de agente.

El modelo conceptual sería:

```text
Recurso de SolidSET
        │
        ▼
SysResourceIA
- IDResource
- Name
- active
        │ 1
        │
        │ N
        ▼
SysChatIAResource
- IDResource
- IDWorkRoom
- IDSession
```

Cada registro de `SysResourceIA` representa un agente, mientras que `SysChatIAResource` determina en qué canales puede participar.

## Flujo propuesto

### 1. Configuración desde SolidSET

Cuando un administrador configure un recurso como agente IA, SolidSET enviaría algo similar a:

```json
{
  "IDResource": "42cc678d-cc28-4e99-b917-a1834a973ff4",
  "Name": "Agente de producción",
  "active": true,
  "workRooms": [
    {
      "IDWorkRoom": "6983dcea-d1ba-4de5-9d7b-53bcc00b65b4"
    }
  ]
}
```

La API debería:

1. Crear o actualizar `SysResourceIA` por `IDResource`.
2. Actualizar el estado `active`.
3. Sincronizar sus canales en `SysChatIAResource`.
4. Eliminar relaciones con canales que ya no estén asignados.
5. Invalidar la configuración almacenada en caché.

Para configuración conviene usar un `UPSERT` por `IDResource`, no crear un registro nuevo en cada envío.

### 2. Recepción de mensajes

Cuando llegue una notificación de SolidSET:

1. Obtener `IDWorkRoom` del mensaje.
2. Buscar los agentes asociados a ese canal:

```sql
SELECT ria.*
FROM public."SysResourceIA" ria
INNER JOIN public."SysChatIAResource" cr
    ON cr."IDResource" = ria."IDResource"
WHERE cr."IDWorkRoom" = :id_workroom
  AND ria.active = true;
```

3. Excluir al propio agente si fue quien envió el mensaje.
4. Comprobar si el mensaje va dirigido a algún agente.
5. Ejecutar solamente los agentes correspondientes.
6. Enviar la respuesta a SolidSET usando el `IDResource` del agente seleccionado.

Es importante impedir que un mensaje producido por un agente active a otro agente y provoque un bucle.

## Varios agentes en el mismo canal

Este es el principal punto que debe definirse. Si hay varios agentes activos en una sala, no deberían responder todos automáticamente.

Las alternativas son:

- Un único agente activo por canal.
- Selección mediante mención: `@AgenteProduccion`.
- Agente principal y agentes secundarios.
- Selección automática según la especialización del agente.

Recomiendo comenzar con un único agente principal por canal. Más adelante puede incorporarse un campo como:

```text
is_primary boolean
priority integer
mention_name varchar
```

Si se permiten varios agentes sin una regla de selección, SolidSET puede recibir respuestas duplicadas o conversaciones entre agentes.

## Separación del aprendizaje

Cada agente necesita distinguir entre tres tipos de conocimiento:

```text
Conocimiento global
├── Manuales, procesos y documentación compartida
│
Conocimiento por agente
├── Experiencia y especialización del IDResource
│
└── Memoria de conversación
    └── IDResource + IDWorkRoom + usuario/conversación
```

En Qdrant se podrían conservar los documentos en una misma colección, pero cada punto debería incluir metadatos como:

```json
{
  "agent_resource_id": "42cc678d-cc28-4e99-b917-a1834a973ff4",
  "workroom_id": "6983dcea-d1ba-4de5-9d7b-53bcc00b65b4",
  "scope": "agent",
  "source": "solidset_chat"
}
```

Al recuperar conocimiento, el filtro sería:

```text
scope = global
OR agent_resource_id = agente actual
```

Así todos pueden utilizar la documentación corporativa, pero el aprendizaje específico de un agente no contamina a los demás.

## Qué debería aprender cada agente

Un agente debería aprender solamente de:

- Mensajes de los canales que tiene asignados.
- Respuestas y correcciones dirigidas a ese agente.
- Actividades de usuarios autorizados.
- Documentos compartidos con sus canales.
- Feedback explícito recibido sobre sus respuestas.

No conviene que un agente aprenda automáticamente de todo SolidSET. Eso puede producir filtraciones entre departamentos y respuestas fuera de contexto.

## Memoria y sesiones

`IDSession` no debería identificar permanentemente al agente. Las sesiones pueden caducar o cambiar.

La identidad permanente debe ser:

```text
IDResource
```

La memoria conversacional debería usar una clave semejante a:

```text
agent:{IDResource}:room:{IDWorkRoom}:conversation:{conversationId}
```

Redis puede mantener la memoria reciente. PostgreSQL o Qdrant pueden conservar aprendizaje duradero.

`IDSession` podría almacenarse en `SysChatIAResource` solamente si representa una sesión técnica válida de SolidSET. Si es una sesión temporal de autenticación, sería más apropiado almacenarla en Redis con caducidad.

## Activación y desactivación

Cuando `active = false`:

- El agente no debe responder.
- No debe iniciar tareas automáticas.
- Puede seguir recibiendo información para auditoría.
- Debe definirse si continúa aprendiendo o queda completamente suspendido.

Mi recomendación es separar ambos conceptos:

```text
active             → puede responder
learning_enabled   → puede aprender
```

Esto permitiría desactivar respuestas mientras se continúa preparando el conocimiento del agente.

## Componentes recomendados

La implementación podría dividirse en:

```text
Configuración SolidSET
        │
        ▼
Agent Registry
Lee SysResourceIA y SysChatIAResource
        │
        ▼
Agent Router
Decide qué agente procesa cada mensaje
        │
        ├── Agent Context
        │   Identidad, nombre, canales y permisos
        │
        ├── Agent Memory
        │   Redis por agente/canal/conversación
        │
        └── Agent Knowledge
            Qdrant con filtros por agente y canal
```

No es necesario crear una instancia pesada de `MachiningAgent` permanentemente por cada recurso. Puede utilizarse el mismo motor y construir un contexto diferente por `IDResource`. Esto consume menos memoria y facilita actualizar las configuraciones dinámicamente.

## Orden de implementación sugerido

1. Definir el contrato de configuración enviado por SolidSET.
2. Convertir el guardado actual en `UPSERT` por `IDResource`.
3. Añadir sincronización completa de los canales asignados.
4. Crear un registro o servicio de agentes activos.
5. Incorporar el enrutamiento por `IDWorkRoom`.
6. Separar memoria Redis por agente.
7. Etiquetar y filtrar Qdrant por agente y canal.
8. Implementar protección contra respuestas entre agentes.
9. Añadir reglas para varios agentes en un mismo canal.
10. Incorporar auditoría, métricas y activación independiente del aprendizaje.

La primera versión debería limitarse a un agente activo por canal, memoria aislada por `IDResource` y aprendizaje filtrado por sus canales. Es la opción más controlable antes de permitir colaboración entre varios agentes.



-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


Exactamente. En ese caso `SysChatIAResource` funciona como una relación muchos-a-muchos:

```text
Un agente puede estar en varios canales.
Un canal puede tener varios agentes.
```

Cuando SolidSET envíe un mensaje, también deberá indicar qué agentes fueron seleccionados para responder.

## Flujo de un mensaje

SolidSET podría enviar:

```json
{
  "IDWorkRoom": "6983dcea-d1ba-4de5-9d7b-53bcc00b65b4",
  "IDSession": "2a33f199-6977-4646-b664-d14d33468839",
  "RawMessage": "¿Cuál puede ser la causa de esta alarma?",
  "SelectedAgentResourceIds": [
    "42cc678d-cc28-4e99-b917-a1834a973ff4",
    "12cf7cc7-d974-41f1-948d-a07f426242c9"
  ]
}
```

La API debería validar que cada agente:

- Existe en `SysResourceIA`.
- Tiene `active = true`.
- Está relacionado con el `IDWorkRoom`.
- No es el recurso que originó el mensaje.

Después ejecutaría una respuesta independiente por cada agente seleccionado.

## Ejecución independiente

El flujo sería:

```text
Mensaje de SolidSET
        │
        ▼
Validar agentes seleccionados
        │
        ├── Agente de mantenimiento
        │   └── Busca solamente su conocimiento
        │
        ├── Agente de calidad
        │   └── Busca solamente su conocimiento
        │
        └── Agente de producción
            └── Busca solamente su conocimiento
```

Todos reciben la misma pregunta, pero cada uno construye su respuesta con:

- Su propia identidad.
- Sus instrucciones.
- Su memoria.
- Sus documentos.
- Su aprendizaje previo.
- El contexto permitido del canal.

No deberían ver las respuestas de los otros agentes antes de responder, salvo que posteriormente se diseñe un modo colaborativo.

## Aislamiento del conocimiento

Cada documento, mensaje aprendido o corrección debería guardar metadatos como:

```json
{
  "agent_resource_id": "42cc678d-cc28-4e99-b917-a1834a973ff4",
  "workroom_id": "6983dcea-d1ba-4de5-9d7b-53bcc00b65b4",
  "scope": "agent",
  "source": "solidset"
}
```

La búsqueda de conocimiento de cada agente aplicaría:

```text
agent_resource_id = agente seleccionado
OR scope = global
```

También puede incluir:

```text
workroom_id = canal actual
```

De este modo:

- El agente de mantenimiento recupera conocimiento técnico.
- El agente de calidad recupera procedimientos y controles.
- El agente de producción recupera planificación y procesos.
- Todos pueden consultar documentación marcada como global.

## Respuestas enviadas a SolidSET

Cada respuesta debe conservar la identidad del agente:

```json
{
  "IDAgentResource": "42cc678d-cc28-4e99-b917-a1834a973ff4",
  "IDWorkRoom": "6983dcea-d1ba-4de5-9d7b-53bcc00b65b4",
  "IDSession": "2a33f199-6977-4646-b664-d14d33468839",
  "Response": "La alarma puede estar relacionada con..."
}
```

Así SolidSET puede publicar cada respuesta como si procediera del recurso correspondiente.

## Memoria separada

La memoria reciente en Redis debería separarse así:

```text
agent:{IDAgentResource}:room:{IDWorkRoom}:session:{IDSession}
```

Aunque dos agentes respondan en el mismo canal y sesión, cada uno tendrá su propia memoria:

```text
agent:A:room:10:session:20
agent:B:room:10:session:20
```

Esto evita que el historial de un agente modifique involuntariamente la respuesta de otro.

## Aprendizaje

Cuando haya una corrección o feedback, SolidSET deberá indicar a qué agente corresponde:

```json
{
  "IDAgentResource": "42cc678d-cc28-4e99-b917-a1834a973ff4",
  "IDWorkRoom": "6983dcea-d1ba-4de5-9d7b-53bcc00b65b4",
  "IDSession": "2a33f199-6977-4646-b664-d14d33468839",
  "Feedback": "La causa correcta era el sensor de presión."
}
```

Solo ese agente debería aprender la corrección. El resto no debería recibirla automáticamente.

## Control de respuestas en bucle

Cada mensaje generado por un agente debe marcarse, por ejemplo:

```json
{
  "GeneratedByIA": true,
  "IDAgentResource": "..."
}
```

Cuando la API reciba una notificación con `GeneratedByIA = true`, no debe volver a activar otros agentes. Esto es especialmente importante cuando varios agentes están registrados en el mismo canal.

## Ejecución paralela

Cuando se seleccionen varios agentes, pueden ejecutarse en paralelo:

```text
Pregunta
  ├── Agente A ──► respuesta A
  ├── Agente B ──► respuesta B
  └── Agente C ──► respuesta C
```

La API puede devolver:

```json
{
  "IDSession": "2a33f199-6977-4646-b664-d14d33468839",
  "responses": [
    {
      "IDAgentResource": "42cc678d-cc28-4e99-b917-a1834a973ff4",
      "status": "completed",
      "response": "..."
    },
    {
      "IDAgentResource": "12cf7cc7-d974-41f1-948d-a07f426242c9",
      "status": "completed",
      "response": "..."
    }
  ]
}
```

Un fallo de un agente no debería impedir que los demás respondan.

## Ajuste conceptual de las tablas

Las tablas actuales sirven para registrar agentes y canales:

```text
SysResourceIA
- ID
- Name
- IDResource
- active

SysChatIAResource
- IDResource
- IDWorkRoom
```

`IDSession` representa una conversación concreta, no una asignación permanente del agente al canal. Por eso sería más limpio administrarlo en Redis o en una futura tabla de sesiones:

```text
SysAgentIASession
- IDSession
- IDResource
- IDWorkRoom
- CreatedAt
- LastActivityAt
- Status
```

La arquitectura recomendada queda así:

```text
SysResourceIA
    Identidad y activación del agente
        │
        ▼
SysChatIAResource
    Canales donde puede ser seleccionado
        │
        ▼
Agent Router
    Valida los agentes elegidos por SolidSET
        │
        ▼
Ejecuciones independientes
        │
        ├── Memoria aislada
        ├── Conocimiento aislado
        └── Respuesta con identidad propia
```

La clave del diseño es que seleccionar varios agentes produzca varias ejecuciones independientes, no una única respuesta generada mezclando el conocimiento de todos.