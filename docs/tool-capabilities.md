# Capacidades y permisos de herramientas

El contrato único está en `app/agent/capabilities.py`. Se aplica al registro de
herramientas, al contexto SQL, al adaptador web y al diálogo entre gemelos.

| Configuración en Capabilities | Permisos efectivos |
| --- | --- |
| `sql` | `solidset_sql`, `solidset_schema` |
| `solidset_sql` | Consulta SQL autorizada |
| `solidset_schema` | Lectura de esquema |
| `external_web` | Búsqueda web |
| `tool:query_sql_server` | `solidset_sql` |
| `tool:get_db_schema` | `solidset_schema` |
| `tool:google_web_search` | `external_web` |
| `tool:<nombre registrado>` | Acceso explícito a esa herramienta |
| `general`, `reasoning`, `coding`, `analysis`, `technical` | Selección del modelo; ningún permiso operativo |

Los permisos del gemelo se obtienen de sus asignaciones activas. Se normalizan
mayúsculas y espacios; se admiten listas y listas JSON de configuraciones antiguas.
Los permisos desconocidos no conceden acceso implícito. Las políticas del registro
determinan el permiso que exige cada herramienta. No existe un comodín que habilite
todas las acciones. Las herramientas de envío o modificación requieren su nombre
explícito y siguen sujetas a los controles de autorización y validación existentes.

Antes de entrar al flujo del agente se resuelven los permisos desde la configuración
del recurso; no se confía en permisos suministrados por el mensaje. Tanto la lista
ofrecida al modelo como la ejecución usan el mismo contrato. Las invocaciones
internas heredadas sin contexto conservan su comportamiento y deben reservarse a
operaciones deterministas autorizadas del servidor.

No es necesario cambiar la configuración existente que declara `sql` para permitir
lectura del esquema. No se han modificado asignaciones persistidas. Las herramientas
sin política explícita, antes sin control en el registro, ahora exigen
`tool:<nombre registrado>` cuando se ejecutan con contexto de agente.

Validación pendiente por el operador: sql debe permitir query_sql_server y
get_db_schema; general no debe permitirlas; external_web no debe permitir SQL;
una herramienta de acción debe rechazarse sin su permiso explícito. Comprobar lo
mismo con listas JSON, mayúsculas y múltiples asignaciones activas, y en los flujos
de sugerencias y diálogo. No se ejecutaron pruebas ni se desplegaron estos cambios.
