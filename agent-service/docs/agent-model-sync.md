# Sincronización de SysAgentIAModel

Implementación local pendiente de pruebas; no se ha ejecutado contra el servidor.

La sincronización completa asignaciones predeterminadas para los recursos activos de `SysSolidSETInstanceResource`, aisladas por instancia. Se ejecuta al instalar el esquema en el arranque, al insertar/actualizar una pertenencia de recurso y al registrar/activar un proveedor Ollama. No añade un planificador periódico ni reactiva DB_STUDY.

También se puede invocar:

`POST /api/v1/agent/solidset/agent-models/sync?instanceCode=<codigo-instancia>`

El endpoint usa los recursos ya sincronizados en PostgreSQL. No importa recursos desde SolidSET ni genera respuestas del modelo. Devuelve los contadores `sourceRows`, `synchronized`, `inserted`, `promoted`, `existing`, `skipped` y `skippedNoProvider`.

Una asignación nueva usa un proveedor Ollama activo registrado, priorizando su marca `IsDefault`, después el código `ollama-default` y finalmente el código por orden. Sus valores son `Role=general`, `LocalExecution=true`, `Capabilities=["general"]`, `Priority=100`, `IsDefault=true`, `active=true`. Los defaults del esquema completan `TrainingMode=rag_reinforcement` y los tres indicadores de aprendizaje en `true`.

Una asignación predeterminada existente no se modifica. Si ya existe una asignación activa al proveedor elegido, solo se promueve su `IsDefault`, conservando capacidades, prioridad y aprendizaje. Las demás asignaciones especializadas o inactivas permanecen intactas. Si no existe un proveedor Ollama activo, se informa `skippedNoProvider`; las pertenencias de recursos pueden sincronizarse y se completarán al registrar el proveedor.

La migración `028_sync_agent_model_defaults.sql` instala las mismas funciones que el esquema de runtime. La sincronización usa bloqueos de pertenencia por instancia/recurso y límites de espera de bloqueo de 5 segundos y ejecución de 30 segundos durante la instalación y la llamada manual.

Validación pendiente: primera sincronización con asignaciones ausentes, segunda ejecución sin cambios, preservación de un default remoto y de especialistas, dos instancias con el mismo recurso y registro posterior de Ollama cuando faltaba proveedor. El backfill del arranque puede haber completado los registros antes de llamar al endpoint; en ese caso aparecerán como `existing`.
