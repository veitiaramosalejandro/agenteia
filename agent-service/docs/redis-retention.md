# Redis: retención y arranque

Cambios locales, pendientes de pruebas. No se ha consultado ni modificado Redis en el servidor.

- El historial `message_store:*` conserva 100 mensajes y caduca tras 7 días desde la última escritura. Las lecturas también están limitadas. Ajustable con `REDIS_HISTORY_MAX_MESSAGES` y `REDIS_HISTORY_TTL_SECONDS`.
- Los estados de respuesta, cachés de embeddings y datos temporales mantienen sus TTL existentes. No se añade caducidad indiscriminada a identidades o conocimiento persistente.
- Las colas de respuestas, sugerencias e histórico usan límites de admisión atómicos. Una cola llena devuelve `QUEUE_FULL` en vez de recortar tareas pendientes. El límite de respuestas baja de 100.000 a 10.000 en configuración y archivos de entorno locales.
- ACK elimina el payload del stream cuando solo existe el grupo de consumo esperado. Una cola vacía caduca tras 24 horas; al encolar se retira esa caducidad. Las colas con trabajo pendiente no reciben TTL.
- La cola diferida de entregas admite hasta 10.000 tareas entre pendientes y en proceso. Reencolar una entrega mueve atómicamente su registro; un fallo al reencolar no borra primero la tarea original.
- Cada 5 minutos se revisan páginas pequeñas del historial legado, consumidores inactivos sin pendientes, mensajes antiguos confirmados y leases interactivos vencidos. No se utilizan `KEYS`, `FLUSHDB` ni eliminación de namespaces desconocidos. El recorte de streams conserva el rango pendiente/no entregado de todos sus grupos.
- Clientes Redis con timeouts y sin reintentos automáticos de socket. Los consumidores conservan un timeout mayor para `XREADGROUP BLOCK`; las comprobaciones de disponibilidad usan PING con timeouts de 1 segundo de conexión y lectura.
- El arranque verifica Redis antes de construir el agente y antes de iniciar las tareas de startup. El health check responde `503` si su comprobación excede 4 segundos. Los diagnósticos restantes también pueden hacer que el health check rechace por timeout; no es una medición exclusiva de Redis.

## Infraestructura preparada

El Compose de producción usa `REDIS_MEMORY_LIMIT=8g`, `REDIS_MAXMEMORY=6gb` y `REDIS_MAXMEMORY_POLICY=allkeys-lru` como valores predeterminados. Los 2 GB restantes dejan margen para overhead y persistencia, pero no garantizan evitar un OOM durante picos o snapshots.

`allkeys-lru` puede expulsar cualquier clave, incluidos streams completos, entregas pendientes, identidades y locks. No garantiza entrega durable: AOF no impide la expulsión. Si las tareas no pueden perderse, estas deben residir en un Redis separado con `noeviction` o tener un respaldo durable recuperable. La política puede cambiarse mediante `REDIS_MAXMEMORY_POLICY`.

Los valores `connected_clients=24`, `blocked_clients=2` y `used_memory_human=2.71G` no identifican por sí solos las claves que ocupan memoria ni prueban el origen del bloqueo. El diagnóstico de consumo y la mejora de latencia quedan pendientes de medición.

## Validación a cargo del usuario

Comprobar TTL y longitud del historial después de escribir; ACK y liberación de payloads; conservación de pendientes durante limpieza; rechazo de cola llena; recuperación de entregas y recreación de grupos. Simular Redis sin respuesta y medir el `503` antes de 5 segundos. Verificar que el arranque aborta y que `maxmemory`, política de expulsión y límite Docker toman los valores previstos. Observar memoria, evicciones, longitudes y pendientes antes/después, sin borrar datos de producción para realizar la prueba.
