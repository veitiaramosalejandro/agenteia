# Presupuesto de contexto Ollama

Implementación local pendiente de pruebas. No requiere cambios adicionales en el servidor realizados por el agente.

- El agente conserva como máximo 3 mensajes del historial conversacional. No reinyecta resúmenes antiguos como instrucciones de sistema.
- Para Ollama se sustituyen los bloques repetidos por una política compacta con identidad, aislamiento, autorización, fecha y contrato de respuesta del modo activo.
- El conocimiento recuperado (documentación, memoria relevante, registros sincronizados, conocimiento privado y aprendizaje) comparte hasta 2.000 caracteres. Los registros del turno, el catálogo SQL y el contexto auxiliar tienen presupuestos separados dentro del límite global.
- Antes de cada llamada del diálogo, incluidos reintentos y síntesis, el contenido de mensajes y argumentos de herramientas queda limitado a 7.999 caracteres. Se conservan completos las políticas, la consulta actual y los argumentos de llamadas. Se priorizan los resultados recientes de herramientas sobre el historial.
- Los recortes de contenido llevan una indicación de omisión. No se modifican los datos originales, Redis ni Qdrant. Las parejas de llamada/resultado conservan sus identificadores.
- Si las instrucciones, la consulta y los argumentos obligatorios no caben, se produce `OLLAMA_PROMPT_BUDGET_EXCEEDED` antes de inferir, en lugar de cortar la consulta o las políticas.
- El proveedor Ollama impone `num_predict <= 400` y toma `num_ctx` de `OLLAMA_CHAT_CONTEXT_LENGTH` (4096 por defecto). El límite de salida también se aplica a otros consumidores de este proveedor; el presupuesto de mensajes corresponde al diálogo de `MachiningAgent`.

Los Compose de desarrollo y producción separan `OLLAMA_CHAT_CONTEXT_LENGTH=4096` de `OLLAMA_EMBEDDING_CONTEXT_LENGTH=2048`. Dentro de cada contenedor Ollama se conserva el nombre nativo `OLLAMA_CONTEXT_LENGTH`, alimentado únicamente por la variable correspondiente a su servicio. Ambos runtimes de embeddings usan la variable de embeddings. La variable global antigua no se usa como fallback.

Si se emplea `.env.production`, seleccionar también ese archivo para la interpolación de Compose (`--env-file .env.production`); `env_file` de un servicio no selecciona las variables de interpolación del Compose. La prioridad de variables del shell sigue aplicándose a los nuevos nombres.

## Validación a cargo del usuario

1. Repetir la consulta problemática con RAG e historial largo: comprobar `OLLAMA_PROMPT_BUDGET after_chars < 8000`, uso correcto del dato recuperado y portugués coherente.
2. Repetir con dos gemelos: comprobar nombres, sujeto consultado y ausencia de contaminación entre memorias.
3. Probar SQL con inspección de esquema y resultados extensos: comprobar que se conservan llamadas/resultados válidos y no se inventan columnas omitidas.
4. Probar sugerencias, refinamiento, consulta general y búsqueda web: verificar el formato JSON y el número solicitado de alternativas dentro de los 400 tokens.
5. Probar una consulta que por sí sola exceda el presupuesto: comprobar rechazo previo a Ollama y ausencia de inferencia con una consulta cortada.
6. Comparar latencia y revisar las métricas de entrada/salida del modelo y sus registros de truncamiento.

El conteo de caracteres no equivale a una medición exacta de tokens: las plantillas del modelo y los esquemas de herramientas enlazadas también consumen contexto y no están incluidos en `after_chars`. El límite de 7.999 caracteres por sí solo no garantiza que toda petición quepa en 4.096 tokens; hay que comprobarlo con el modelo y las herramientas usados en esas pruebas.
