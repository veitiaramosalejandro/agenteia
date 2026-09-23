# Agent Control Center — avance de implementación

Actualizado: 23 de septiembre de 2026.

## Objetivo

Centralizar la administración de instancias SolidSET, agentes especializados,
prompts, asignaciones de modelos y pruebas aisladas sobre la API existente.

## Fases

| Fase | Alcance | Estado |
| --- | --- | --- |
| 1. Base operativa | Aplicación web, selección de instancia, dashboard, agentes, prompts, modelos y laboratorio | Completada |
| 2. Conocimiento | Fuentes, colecciones, ingestión, estado y pruebas de recuperación por agente e instancia | Pendiente |
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

## Criterio para iniciar la fase 2

Diseñar el contrato de administración del conocimiento con aislamiento por
instancia y agente, estados de ingestión visibles y una prueba de recuperación
que no escriba ni publique respuestas.
