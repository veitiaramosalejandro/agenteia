# NVIDIA Kimi K3

Configura `NVIDIA_API_KEY` en el entorno del proceso de agent-service o en su
`.env`. La clave no se guarda en código. No se han desplegado ni reiniciado servicios.

En Swagger (`/docs`) o desde un cliente HTTP:

```http
POST /api/v1/agent/llm/nvidia/test
Content-Type: application/json

{
  "prompt": "What is in this image?",
  "image_url": "https://assets.ngc.nvidia.com/products/api-catalog/phi-3-5-vision/example1b.jpg",
  "stream": true,
  "max_tokens": 16384,
  "seed": 0,
  "temperature": 1,
  "reasoning_effort": "max"
}
```

`image_url` es opcional y admite HTTP(S). El servicio envía la URL a NVIDIA;
no descarga la imagen localmente. El modelo fijo es `moonshotai/kimi-k3` y el
host es `https://integrate.api.nvidia.com/v1`. El endpoint transforma `prompt`
e `image_url` en `messages` con bloques de texto e imagen.

Con `stream: true` (predeterminado) devuelve SSE con los chunks originales del
proveedor, incluido `reasoning_content` si lo envía, y `data: [DONE]` al finalizar.
Un fallo después de abrir el stream se comunica mediante `event: error` con un
código `status` 429, 502 o 504; el HTTP ya será 200. Se cierra la conexión externa
al terminar, cancelar o agotar el plazo. Con `stream: false` devuelve JSON con
`provider`, `model`, `content`, `finish_reason` y `usage`, sin razonamiento interno.

Límites: entrada de 8000 caracteres, 1–16384 tokens, temperatura 0–1 y plazo total
60 s sin reintentos. `reasoning_effort` admite `low`, `high` y `max` (predeterminado).
Por defecto se usan 256 tokens; pueden agotarse durante el razonamiento antes de
producir contenido final. Para reproducir el ejemplo usa el cuerpo anterior.
No se envían `top_p` ni `chat_template_kwargs` a Kimi K3.

Sin streaming: errores 422 entrada inválida, 503 clave ausente, 429 cuota,
504 timeout y 502 fallo del proveedor. Sigue el control de acceso del servicio.
No guarda conversaciones ni asigna modelos a recursos.

Para crear una conexión general cifrada:

```http
POST /api/v1/agent/llm/providers/from-env
Content-Type: application/json

{"Source":"nvidia","Code":"nvidia-kimi-k3","Name":"NVIDIA Kimi K3","IsDefault":false}
```

La asignación a recursos sigue usando `SysAgentIAModel` / `ProviderCode`.
No se modifican conexiones ya guardadas. El adaptador conserva las opciones
Nemotron para las conexiones anteriores con ese modelo.
Para seleccionar Kimi K3 como runtime: `LLM_PROVIDER=nvidia`,
`MODEL_NAME=moonshotai/kimi-k3` y `NVIDIA_API_KEY` (o `LLM_API_KEY`).

Validación local con HTTP simulado; acceso real de la cuenta pendiente.
Referencia: https://docs.api.nvidia.com/nim/reference/moonshotai-kimi-k3-infer
