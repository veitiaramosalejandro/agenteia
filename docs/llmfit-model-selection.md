# Selección de modelos con llmfit

Fecha de evaluación: 20 de agosto de 2026.

## Hardware detectado

- CPU: Intel Core i7-11850H, 16 núcleos lógicos.
- RAM total: 47.48 GB; disponible durante la prueba: 5.86 GB.
- GPU: NVIDIA RTX A3000 Laptop, 6 GB VRAM.
- Producción evaluada adicionalmente: 8 CPU, 32 GB RAM, sin GPU.
- Contexto utilizado para estimación: 4096 tokens.

## Catálogo elegido

| Rol | Modelo | Estado | Motivo |
|---|---|---|---|
| Coordinador y chat general | `qwen2.5:3b` | Activo | Ya está instalado, consume aproximadamente 1.9 GB y deja margen para embeddings y servicios Docker. |
| Especialista de código y SQL | `qwen2.5-coder:3b` | Bajo demanda | llmfit lo clasifica `Perfect`, estima 3.88 GB y aproximadamente 62.7 tokens/s con la GPU detectada. |
| Razonamiento complejo | `Phi-4-mini-reasoning` | Opcional, secuencial | llmfit lo clasifica `Perfect`, estima aproximadamente 5.03 GB; no debe coexistir en VRAM con otro modelo grande. |
| Embeddings actuales | `nomic-embed-text` | Activo | Mantiene compatibilidad con la colección Qdrant existente y tiene una huella pequeña. |
| Embeddings futuros | `Qwen3-Embedding-0.6B` | Migración futura | llmfit lo clasifica `Perfect`, estima 1.56 GB y aproximadamente 325 tokens/s; requiere una colección nueva y reindexado completo. |

## Catálogo local registrado en PostgreSQL

- `ollama-default` → `qwen2.5:3b`, coordinación y conversación general.
- `ollama-coder` → `qwen2.5-coder:3b`, código, SQL e integración técnica.
- `ollama-secondary` → `llama3.2:3b`, agente general alternativo.

Los modelos están instalados en Ollama y se asignan a cada recurso mediante
`SysAgentIAModel.ProviderCode`. `nomic-embed-text` permanece como modelo de
embeddings compartido y no genera respuestas.

## Reglas operativas

- Mantener `OLLAMA_MAX_LOADED_MODELS=1` y `OLLAMA_NUM_PARALLEL=1` en el perfil CPU/base.
- Cargar los especialistas solo cuando el router los necesite.
- No usar simultáneamente el razonador y otro modelo grande en la GPU de 6 GB.
- No cambiar el modelo de embeddings de una colección Qdrant existente.
- Los proveedores cloud no se eligen por ajuste de hardware local; su modelo se configura por coste, privacidad, latencia y capacidades del contrato.

## Comandos utilizados

```powershell
llmfit system --json
llmfit --max-context 4096 --json recommend -n 5 --use-case general --min-fit good
llmfit --max-context 4096 --json recommend -n 5 --use-case coding --min-fit good
llmfit --max-context 4096 --json recommend -n 5 --use-case reasoning --min-fit good
llmfit --max-context 4096 --json recommend -n 5 --use-case embedding --min-fit good
llmfit --memory 0G --ram 32G --cpu-cores 8 --max-context 4096 --json recommend -n 8 --use-case general --capability tool_use --min-fit good
```
