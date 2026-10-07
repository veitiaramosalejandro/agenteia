# Prompts operativos de prueba SOLIDSET

Este guion valida que el agente se comporta como un usuario real de SOLIDSET y respeta autenticacion antes de consumir endpoints.

## 1) Arranque de sesion autenticada

Prompt 1:
Autenticate contra SOLIDSET y dime base activa, endpoint de login usado y si hay cookie/access key.

Prompt 2 (forzar relogin):
Reautenticate forzando nuevo login en SOLIDSET y confirma estado final de sesion.

Resultado esperado:
- El agente usa primero la herramienta de autenticacion.
- Reporta base URL activa y estado de credenciales de sesion.

## 2) Chat: destinos y mensajes

Prompt 3:
Lista mis destinos de chat/canales disponibles en SOLIDSET (modo 1, sin read pointers, sin tabs) y resumelos en formato legible.

Prompt 4:
Lee los ultimos 10 mensajes del canal 6983dcea-d1ba-4de5-9d7b-53bcc00b65b4 para el login bb132fdd-97cb-4784-8d94-1c6f1c27b090.

Prompt 5:
Consulta tareas del canal 6983dcea-d1ba-4de5-9d7b-53bcc00b65b4 con estados [2723,2724,2725,2731,2732].

Resultado esperado:
- El agente consume tools de chat especializadas.
- Devuelve resumen funcional, no volcado crudo excesivo.

## 3) Point: tareas y actividades

Prompt 6:
Dame el detalle de la tarea 4124d36c-a806-ed11-a401-60a44c4f53f0 en Point.

Prompt 7:
Dame informacion de la actividad 2436645 usando modulo 78c873aa-bdf4-4f1a-9f63-6a8fbb788b95.

Prompt 8:
Lee tareas/actividades Point para el recurso 5226aeac-d518-4e74-84c3-e6f982729b59 con readActivities=true y onlyTasksAssignedToMe=false.

Resultado esperado:
- El agente usa endpoints de Point con sesion autenticada activa.

## 4) Vehicle

Prompt 9:
Consulta el vehiculo con ResourceID 759fd06f-e6cb-41c7-9756-e23342aa0f22 incluyendo ultimos logs (page size 70, page 1).

Resultado esperado:
- El agente devuelve informacion del vehiculo y resumen de logs.

## 5) Feature Flags

Prompt 10:
Consulta feature flags del recurso 4eeb7c8f-84bd-4876-9712-ca43d12fd226.

Prompt 11:
Consulta las feature flags globalmente activadas en SOLIDSET.

Resultado esperado:
- El agente usa tools de feature flags y resume estado activado.

## 6) Pruebas de escritura con confirmacion

Prompt 12 (debe pedir confirmacion o exigir confirm=true):
Envia el mensaje "Prueba agente" al canal debf64b2-3b3e-eb11-870c-d850e63f5833.

Prompt 13 (confirmado):
Envia el mensaje "Prueba agente confirmada" al canal debf64b2-3b3e-eb11-870c-d850e63f5833 con confirm=true.

Resultado esperado:
- Sin confirm=true, no ejecuta escritura.
- Con confirm=true, ejecuta envio real.

## 7) Cierre de sesion

Prompt 14:
Cierra sesion SOLIDSET y confirma resultado.

Resultado esperado:
- El agente ejecuta logoff y limpia sesion local.

## 8) Prompt comodin para endpoints no cubiertos

Prompt 15:
Llama al endpoint /Chat/GetEmailList con metodo GET y query {"page":0,"size":10,"SearchTerm":"","order":1,"idChannel":"9ec464b2-3b3e-eb11-870c-d850e63f5833"} usando sesion autenticada.

Resultado esperado:
- El agente usa la herramienta generica autenticada para endpoints no envueltos.

## Checklist rapido de validacion

- Primero autentica antes de operaciones SOLIDSET.
- Reutiliza sesion entre prompts consecutivos.
- Si recibe 401/403, reintenta con reautenticacion.
- Requiere confirmacion en operaciones de escritura.
- Responde con resumen legible de negocio.
