# Agent Control Center — avance de implementación

Actualizado: 23 de septiembre de 2026.

## Objetivo

Centralizar la administración de instancias SolidSET, agentes especializados,
prompts, asignaciones de modelos y pruebas aisladas sobre la API existente.

## Fases

| Fase | Alcance | Estado |
| --- | --- | --- |
| 1. Base operativa | Aplicación web, selección de instancia, dashboard, agentes, prompts, modelos y laboratorio | Completada |
| 2. Conocimiento | Fuentes, colecciones, ingestión, estado y pruebas de recuperación por agente e instancia | Completada |
| 3. Automatización | Canales, reglas de respuesta, capacidades, límites y tareas controladas | Pendiente |
| 4. Observabilidad | Trazas, tiempos por etapa, consumo, errores, auditoría y exportación | Pendiente |
| 5. Gobierno | Autenticación, roles, permisos, aprobación, historial y restauración | Pendiente |

## Fase 1: módulos entregados

### Selector de instancia

- Obtiene las instancias desde la API y conserva la selección localmente.
- Toda consulta y modificación dependiente de SolidSET utiliza la instancia
  seleccionada.
- Presenta estado, país, idioma y URLs operativas sin exponer credenciales.

### Resumen operativo

- Muestra cantidades de instancias, agentes y proveedores.
- Resume el proveedor y modelo principal de la instancia seleccionada.
- Expone el estado de respuesta automática y diálogos habilitados.

### Instancias

- Lista la configuración pública de cada instancia.
- Ejecuta la prueba de conexión ya disponible en la API.
- Diferencia la API de SolidSET de su proveedor de datos.

### Agentes

- Añade un endpoint de lectura agrupado por instancia.
- Muestra por separado `IDResource` e `IDAgentResource`.
- Resume perfil, ámbitos, prompt publicado y modelos asignados.

### Prompts

- Editor estructurado para nombre, rol, objetivo, especialidades,
  instrucciones de revisión, formato, restricciones, tono e idioma.
- Generación de borrador y vista previa del prompt de sistema.
- Publicación mediante los endpoints existentes y con instancia explícita.

### Modelos

- Lista proveedores configurados.
- Permite asignar proveedor, modelo, capacidades, prioridad y modelo por
  defecto al recurso y a la instancia seleccionados.

### Laboratorio

- Ejecuta diálogos de prueba sin enviarlos a SolidSET.
- Mantiene el identificador de sesión para pruebas consecutivas.
- Muestra respuestas y errores de forma visible.

## Controles incluidos

- El frontend usa rutas relativas y un proxy interno de Nginx.
- Producción publica el puerto únicamente en `127.0.0.1` hasta incorporar
  autenticación y autorización en la fase 5.
- No se muestran claves ni cadenas de conexión.
- Las operaciones se realizan con el código de instancia y el recurso
  seleccionados de forma explícita.

## Validación de la fase

- Compilación TypeScript y Vite.
- Construcción de la imagen Docker de producción.
- Validación de Docker Compose para desarrollo y producción.
- Prueba unitaria del endpoint de agentes, verificando que todas las lecturas
  reciben el identificador de la misma instancia.
- Comprobación HTTP del contenedor web.

## Fase 2: módulos entregados

### Fuentes de conocimiento

- Inventario de fuentes activas e inactivas por instancia y `IDResource`.
- Alta de textos verificados con título, procedencia y canal opcional.
- Persistencia en PostgreSQL e indexación inmediata en Qdrant.
- Reindexación manual y desactivación lógica con retirada del punto vectorial.
- Las fuentes desactivadas se conservan en PostgreSQL para auditoría.

### Ingestión del conocimiento del sistema

- Inicio de ejecuciones para una instancia explícita.
- Selección opcional de tablas autorizadas.
- Visualización de estado, progreso, tablas, filas y puntos indexados.
- La clave administrativa se mantiene únicamente en memoria del navegador.
- Se reutilizan la cola, el worker y los controles de concurrencia existentes.

### Laboratorio RAG

- Búsqueda semántica privada con aislamiento por instancia y agente.
- Filtro opcional por canal y umbral de similitud configurable.
- Consulta separada del conocimiento privado y la fotografía del sistema.
- Presentación del contexto recuperado y número de coincidencias.
- No invoca un LLM, no aprende de la consulta y no publica en SolidSET.

### Contratos administrativos añadidos

- Listar fuentes de un agente.
- Desactivar una fuente.
- Reindexar una fuente concreta.
- Probar recuperación RAG privada y del sistema.

## Validación adicional de la fase 2

- Nueve pruebas unitarias superadas, incluyendo aislamiento de listado y RAG,
  y conservación de la fuente activa si Qdrant no confirma su retirada.
- Compilación Python de controladores, esquemas y acceso a datos.
- Compilación de producción React, TypeScript y Vite.

## Criterio para iniciar la fase 3

Diseñar la administración de canales, capacidades y reglas de respuesta con
validación previa de permisos, límites de ejecución y trazabilidad de cada
automatización.
