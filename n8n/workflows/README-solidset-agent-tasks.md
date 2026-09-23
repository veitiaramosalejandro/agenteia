# Workflow SolidSET Agent - Chat y tareas controladas

Importa `solidset-agent-tasks.json` desde **Workflows > Import from File** en n8n.

El webhook `POST /webhook/solidset-agent-tasks` admite tres operaciones:

- `ask`: consulta al agente y, opcionalmente, publica su respuesta en SolidSET.
- `teach`: guarda e indexa conocimiento privado del agente.
- `health`: comprueba la API del agente.

Antes de activar el flujo, configura autenticación **Header Auth** en el nodo `Entrada segura`. Los nodos HTTP utilizan directamente `http://agent-service:8000`, el nombre DNS interno disponible en la red Docker compartida. Esto evita depender del acceso a variables de entorno, que puede estar bloqueado en n8n.

## Consultar al agente

```json
{
  "operation": "ask",
  "instanceCode": "local-developer",
  "agentResourceId": "RECURSO-HUMANO-DEL-GEMELO",
  "senderResourceId": "RECURSO-QUE-PREGUNTA",
  "workRoomId": "CANAL-SOLIDSET",
  "message": "Revisa este codigo C# y propone mejoras.",
  "sendToSolidSET": false
}
```

`agentResourceId` corresponde al `IDResource` humano cuyo gemelo debe responder. El agente debe estar activo y asignado a `workRoomId`. Conserva el `sessionId` devuelto y envíalo en las preguntas siguientes para mantener la conversación.

Para publicar la respuesta en el canal, usa `sendToSolidSET: true`. En ese caso, `instanceCode` es obligatorio.

## Enseñar conocimiento

```json
{
  "operation": "teach",
  "instanceCode": "local-developer",
  "agentResourceId": "RECURSO-HUMANO-DEL-GEMELO",
  "workRoomId": "CANAL-SOLIDSET",
  "title": "Convenciones del proyecto",
  "knowledge": "Los controladores deben delegar la logica de negocio en servicios.",
  "source": "n8n",
  "active": true
}
```

## Comprobar salud

```json
{
  "operation": "health"
}
```

Para incorporar nuevas tareas, añade una salida al nodo `Seleccionar tarea` y conéctala a un nodo con credenciales y permisos mínimos. Mantén una lista explícita de operaciones; no uses como URL, SQL o comando un valor generado libremente por el modelo.
