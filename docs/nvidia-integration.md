# Varios modelos con la API de NVIDIA

Configura `NVIDIA_API_KEY` en el entorno del proceso de agent-service o en su
`.env`. La clave no se guarda en código. No se han desplegado ni reiniciado servicios.

En Swagger (`/docs`) o desde un cliente HTTP:

```http
POST /api/v1/agent/llm/nvidia/test
Content-Type: application/json

{
  "model": "moonshotai/kimi-k3",
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
no descarga la imagen localmente. El campo `model` selecciona el modelo de la prueba y el
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

## Registrar y elegir modelos

Crea una configuración general por modelo, con un `Code` único. Comparten la API
NVIDIA y pueden usar la misma `NVIDIA_API_KEY`; cada registro guarda su credencial
cifrada según el esquema existente. `Model` es obligatorio con `Source=nvidia`.
No hay que cambiar el código ni el modelo global para registrar otro modelo.

Primera petición para registrar Kimi:

```http
POST /api/v1/agent/llm/providers/from-env
Content-Type: application/json

{"Source":"nvidia","Code":"nvidia-kimi-k3","Name":"NVIDIA Kimi K3","Model":"moonshotai/kimi-k3","IsDefault":false}
```

La asignación a recursos sigue usando `SysAgentIAModel` / `ProviderCode`.
No se modifican conexiones ya guardadas. El adaptador conserva las opciones
Nemotron para las conexiones anteriores con ese modelo.
Para seleccionar Kimi K3 como runtime: `LLM_PROVIDER=nvidia`,
`MODEL_NAME=moonshotai/kimi-k3` y `NVIDIA_API_KEY` (o `LLM_API_KEY`).

Validación local con HTTP simulado; acceso real de la cuenta pendiente.
Referencia: https://docs.api.nvidia.com/nim/reference/moonshotai-kimi-k3-infer

Segunda petición al mismo `POST /api/v1/agent/llm/providers/from-env`:

```json
{"Source":"nvidia","Code":"nvidia-nemotron","Name":"NVIDIA Nemotron","Model":"nvidia/nemotron-3-ultra-550b-a55b","IsDefault":false}
```

Lista los registros con `GET /api/v1/agent/llm/providers`. Para modificar una
configuración existente usa `PUT /api/v1/agent/llm/providers/{code}`, indicando
`Code`, `Name`, `Provider: nvidia` y `Model`. Puedes omitir `APIKey` para conservar
la clave ya guardada. Cambiar ese registro afecta a todos los agentes que lo usan.
Para conservar ambos modelos, crea dos códigos en lugar de sobrescribir uno.

Asigna el modelo al recurso mediante:

```http
PUT /api/v1/agent/solidset/agents/{IDResource}/model
Content-Type: application/json

{"ProviderCode":"nvidia-kimi-k3","LocalExecution":false,"Capabilities":["general"],"Priority":0,"IsDefault":true}
```

Para cambiar a Nemotron, usa `ProviderCode: nvidia-nemotron`. Las asignaciones son
independientes por recurso y pueden coexistir por capacidad. El selector existente
prioriza coincidencias de `Capabilities` y después menor `Priority`; `IsDefault`
es fallback y por sí solo no anula otras coincidencias. Si ambos modelos tienen
la misma capacidad, evita empates: cambia la asignación anterior a `Priority:100`
o desactívala con `active:false` usando el mismo PUT. Comprueba las asignaciones
con `GET /api/v1/agent/solidset/agents/{IDResource}/model`.

El runtime usa el `Model` del registro seleccionado, y la API invalida su caché
local al guardar configuraciones o asignaciones. No se han escrito registros en
la base de datos durante esta implementación ni se ha desplegado el servicio.

Otros modelos NVIDIA usan parámetros comunes de Chat Completions. Las opciones
específicas de Kimi K3 y Nemotron Ultra solo se envían a esos modelos. La capacidad
de imágenes y herramientas depende del modelo elegido y debe verificarse con
NVIDIA. La ruta de prueba admite `model` directamente y usa la clave del entorno;
no cambia las asignaciones de agentes.
