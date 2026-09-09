# NVIDIA Nemotron

Configura `NVIDIA_API_KEY` en el entorno del proceso de agent-service (o en el
`.env` que carga ese proceso). Nunca incluyas la clave en código ni en ejemplos.
Los cambios del entorno se cargan al iniciar el proceso; esta implementación no
reinicia servicios ni despliega contenedores.

Prueba desde Swagger (`/docs`) o con:

```http
POST /api/v1/agent/llm/nvidia/test
Content-Type: application/json

{"prompt":"Explica qué es una GPU en una frase.","max_tokens":256,"enable_thinking":false}
```

Devuelve `provider`, `model`, `content`, `finish_reason` y `usage`. Usa el modelo
`nvidia/nemotron-3-ultra-550b-a55b` y la URL fija
`https://integrate.api.nvidia.com/v1`. No guarda conversaciones ni asigna modelos.
La prueba devuelve JSON completo, no SSE; no devuelve el razonamiento interno.
Admite hasta 16384 tokens, 8000 caracteres de entrada y un plazo total de 60 s,
sin reintentos. `finish_reason: length` indica truncamiento; con razonamiento
activado puede agotarse el presupuesto antes de producir contenido final.
Errores: 422 entrada inválida, 503 clave ausente, 429 cuota, 504 timeout y 502
fallo del proveedor. Sigue el control de acceso existente del servicio.

Para guardar una conexión general cifrada usa el endpoint existente:

```http
POST /api/v1/agent/llm/providers/from-env
Content-Type: application/json

{"Source":"nvidia","Code":"nvidia-nemotron","Name":"NVIDIA Nemotron","IsDefault":false}
```

La asignación a un recurso sigue usando `SysAgentIAModel` / `ProviderCode`.
También se admite `Provider: nvidia` en el PUT de proveedores. El adaptador
LangChain soporta invoke, stream y bind_tools mediante Chat Completions y activa
`chat_template_kwargs.enable_thinking`; omite opciones exclusivas de OpenAI.
Para seleccionar NVIDIA como runtime, usa `LLM_PROVIDER=nvidia`,
`MODEL_NAME=nvidia/nemotron-3-ultra-550b-a55b` y `LLM_API_KEY` con la clave NVIDIA.

Referencia: https://docs.api.nvidia.com/nim/reference/llm-apis
Validación local con transporte HTTP simulado; disponibilidad y acceso de la
cuenta al modelo requieren una prueba real posterior.
