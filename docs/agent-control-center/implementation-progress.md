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
| 3. Automatización | Canales, reglas de respuesta, capacidades, límites y tareas controladas | Completada |
| 4. Observabilidad | Trazas, tiempos por etapa, consumo, errores, auditoría y exportación | Completada |
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

## Fase 3: módulos entregados

### Canales y agentes

- Inventario de canales sincronizados por instancia.
- Visualización de descripción, código, estado y asignaciones.
- Activación y desactivación de agentes por canal.
- Orden de respuesta configurable.
- La escritura utiliza `SysSolidSETInstanceChatIAResource`; se mantiene la
  relación de compatibilidad solamente dentro de la misma instancia.

### Reglas de respuesta

- Reglas manuales o aplicables a mensajes dirigidos al agente.
- Instrucción operativa acotada, capacidades requeridas y límite horario.
- Aprobación configurable para la generación.
- Desactivación lógica para conservar historial.
- Las reglas automáticas se comprueban durante el enrutamiento real; una regla
  con aprobación pendiente, capacidades ausentes o límite agotado no responde.

### Tareas controladas

- Evaluación previa sin ejecutar el modelo.
- Comparación visible de capacidades requeridas y disponibles.
- Registro de motivos de bloqueo y consumo horario.
- Ejecución reutilizando el diálogo aislado del agente.
- Vista previa sin publicación como comportamiento predeterminado.
- Todo envío a SolidSET requiere aprobación explícita para esa ejecución.

### Persistencia y auditoría técnica

- Nueva tabla `SysAgentIAAutomationRule` aislada por instancia, agente y canal.
- Nueva tabla `SysAgentIAAutomationRun` para bloqueos, ejecuciones, salidas,
  aprobaciones y estado de entrega.
- Inicialización compatible con bases nuevas y volúmenes PostgreSQL existentes.

## Validación adicional de la fase 3

- Trece pruebas unitarias superadas en los módulos enfocados.
- Casos específicos para aislamiento de canal, asignación activa, capacidades,
  aprobación y bloqueo del envío sin autorización.
- Compilación Python y compilación de producción React/TypeScript.

## Criterio para iniciar la fase 4

Unificar auditorías, tiempos por etapa, consumo, ejecuciones de automatización y
errores en vistas consultables y exportables sin exponer información sensible.

## Fase 4: módulos entregados

### Vista operativa unificada

- Eventos de respuestas automáticas, herramientas, ingestiones y reglas.
- Aislamiento por instancia y filtro opcional por agente.
- Ventanas de una hora, 24 horas, siete días y 30 días.
- Filtros por tipo y estado.
- Actualización manual o automática cada 15 segundos.

### Métricas

- Volumen de eventos y distribución por tipo.
- Completados, fallos, bloqueos y tasa de éxito.
- Duración media y percentil 95 de los eventos cargados.
- Métricas en memoria del diálogo: cantidad, última duración, máxima duración
  y aciertos de caché.
- Los conteos representan consumo operativo. No se presentan tokens o costes
  cuando el proveedor no los registra de forma verificable.

### Auditoría segura

- Las respuestas nuevas guardan explícitamente `IDSolidSETInstance`.
- Los registros visibles omiten payloads, mensajes, prompts, argumentos de
  herramientas, credenciales y respuestas generadas.
- Los errores se limitan a 500 caracteres.
- Los registros históricos sin instancia explícita no se atribuyen por
  inferencia a una instancia.

### Exportación

- Exportación CSV con los mismos filtros de la vista.
- Límite máximo de 1000 eventos por exportación.
- Neutralización de valores que podrían interpretarse como fórmulas por una
  hoja de cálculo.

### Persistencia

- Migración `029_scope_response_audit_by_instance.sql`.
- Índice por instancia y fecha para la auditoría de respuestas.
- Compatibilidad con volúmenes PostgreSQL existentes mediante actualización
  idempotente del esquema.

## Validación adicional de la fase 4

- Quince pruebas unitarias enfocadas superadas.
- Pruebas de aislamiento por instancia y protección de la exportación CSV.
- Veintisiete pruebas de enrutamiento multiagente superadas dentro del
  contenedor, incluyendo auditoría, selección por instancia y entrega.
- Esquemas idempotentes y consulta agregada verificados contra PostgreSQL local.
- Compilación Python y compilación de producción React/TypeScript.

## Criterio para iniciar la fase 5

Incorporar autenticación administrativa, roles, permisos por operación,
aprobaciones durables, historial de cambios y restauración controlada.

## Fase 5: módulos entregados

### Autenticación administrativa

- Inicio y cierre de sesión mediante tokens aleatorios revocables.
- En PostgreSQL se conserva solamente el hash SHA-256 del token.
- Contraseñas derivadas con PBKDF2-SHA256, 310 000 iteraciones y sal individual.
- Caducidad configurable y revocación de todas las sesiones al desactivar un usuario.
- Creación segura del primer administrador mediante variables de entorno, únicamente
  cuando no existe ningún usuario administrativo.

### Roles y permisos

- `administrator`: configuración, operación, aprobaciones, restauración y usuarios.
- `operator`: lectura, configuración, operación y solicitud de aprobaciones.
- `auditor`: acceso de solo lectura.
- La API aplica los permisos a instancias, agentes, prompts, modelos, conocimiento,
  automatizaciones, ingestión y observabilidad cuando el control de acceso está activo.

### Aprobaciones durables

- Solicitudes ligadas a instancia, operación, tipo de recurso, identificador y motivo.
- Estados pendiente, aprobado, rechazado, consumido y cancelado.
- Registro del solicitante, decisor, nota y fechas de decisión y consumo.
- Una aprobación de restauración solo puede consumirse una vez y debe referirse
  exactamente al cambio solicitado.

### Historial y restauración

- Auditoría de las mutaciones administrativas con actor, método, ruta y resultado HTTP.
- Instantáneas explícitas antes y después de desactivar reglas de automatización.
- Restauración limitada a campos permitidos de reglas y ejecutada en una transacción.
- No se almacenan contraseñas, tokens, claves, prompts, mensajes ni cuerpos de petición
  en el historial genérico.

### Consola web

- Pantalla de acceso y sesión mantenida únicamente durante la pestaña del navegador.
- Vista de aprobaciones con decisión según permisos.
- Historial por instancia y flujo de solicitud, aprobación y restauración.
- Administración de usuarios, roles y activación de cuentas.
- Exportación de observabilidad autenticada mediante descarga con cabecera Bearer.

### Persistencia

- Migración `030_create_control_center_governance.sql`.
- Tablas de usuarios, sesiones, aprobaciones e historial, con índices de consulta.
- Inicialización idempotente para bases nuevas y volúmenes existentes.

## Validación adicional de la fase 5

- Veintiuna pruebas enfocadas de gobierno y módulos administrativos superadas.
- Treinta y tres pruebas de gobierno y enrutamiento ejecutadas dentro del contenedor.
- Verificación HTTP 401 de una ruta administrativa sin sesión válida.
- Creación idempotente de las cuatro tablas comprobada contra PostgreSQL local.
- Compilación React/TypeScript y construcción de la imagen `agent-control-center:0.5.0`.
- Configuración Compose validada para desarrollo y producción con GPU.
- La validación no creó usuarios, sesiones ni contraseñas de prueba.

## Criterio para iniciar la fase 6

Incorporar políticas por instancia y recurso, autenticación corporativa mediante OIDC,
rotación obligatoria de credenciales, segundo aprobador configurable y alertas de
seguridad integradas con la infraestructura de la organización.
