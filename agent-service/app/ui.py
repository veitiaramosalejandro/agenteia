import os
import requests
import streamlit as st
import uuid
from datetime import datetime

# ============================================================
# CONFIGURACIÓN DE LA PÁGINA
# ============================================================

st.set_page_config(
    page_title="Assistant AI",
    page_icon="⚙️",
    layout="centered",
    initial_sidebar_state="expanded"
)

# Estilos CSS personalizados
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1f77b4;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1rem;
        color: #666;
        margin-bottom: 1.5rem;
    }
    .user-badge {
        background-color: #e8f4fd;
        padding: 0.5rem 1rem;
        border-radius: 20px;
        font-size: 0.9rem;
        border: 1px solid #cce5f5;
    }
    .canal-tag {
        background-color: #f0f2f6;
        padding: 0.2rem 0.6rem;
        border-radius: 12px;
        font-size: 0.75rem;
        margin: 0.1rem;
        display: inline-block;
    }
    .stChatMessage {
        padding: 0.5rem 1rem;
    }
</style>
""", unsafe_allow_html=True)

# ============================================================
# CONFIGURACIÓN DE URLS
# ============================================================

BASE_API_URL = os.getenv("API_URL", "http://agent-service:8000/api/v1/agent")

if BASE_API_URL.endswith("/"):
    BASE_API_URL = BASE_API_URL[:-1]
if BASE_API_URL.endswith("/dialogue"):
    BASE_API_URL = BASE_API_URL.replace("/dialogue", "")

DIALOGUE_URL = f"{BASE_API_URL}/dialogue"
HISTORY_URL = f"{BASE_API_URL}/history"
CONTEXT_URL = f"{BASE_API_URL}/context"
HEALTH_URL = f"{BASE_API_URL}/health"

# ============================================================
# FUNCIONES DE UTILIDAD
# ============================================================

def verificar_servicio():
    """Verifica que el backend esté disponible."""
    try:
        response = requests.get(HEALTH_URL, timeout=5)
        return response.status_code == 200
    except:
        return False

def obtener_contexto_usuario(user_id: str):
    """Obtiene el contexto del usuario desde el backend."""
    try:
        response = requests.get(f"{CONTEXT_URL}/{user_id}", timeout=10)
        if response.status_code == 200:
            return response.json()
        return None
    except:
        return None

def generar_texto_conversacion(messages, session_id, user_id):
    """Genera un archivo de texto con la conversación completa."""
    texto = f"""--- HISTORIAL DE CONVERSACIÓN ---
Sesión: {session_id}
Usuario: {user_id}
Fecha de exportación: {datetime.now().strftime('%d/%m/%Y %H:%M')}
{'-' * 50}

"""
    for msg in messages:
        rol = "🧑 OPERARIO" if msg["role"] == "user" else "🤖 ASISTENTE"
        texto += f"[{rol}]: {msg['content']}\n\n"
    
    texto += f"\n{'-' * 50}\nFin del historial - {len(messages)} mensajes"
    return texto

def limpiar_sesion():
    """Limpia la sesión actual y crea una nueva."""
    import uuid
    new_session = f"session_{uuid.uuid4().hex[:8]}"
    st.query_params["session_id"] = new_session
    st.session_state["session_id"] = new_session
    st.session_state["messages"] = [
        {"role": "assistant", "content": f"👋 ¡Hola! Soy tu asistente de SolidSET. ¿En qué puedo ayudarte hoy?"}
    ]
    st.session_state["messages_loaded"] = True
    st.rerun()

# ============================================================
# INICIALIZACIÓN DEL ESTADO DE SESIÓN
# ============================================================

# Verificar salud del servicio
if "service_available" not in st.session_state:
    st.session_state["service_available"] = verificar_servicio()

if not st.session_state["service_available"]:
    st.error("⚠️ No se puede conectar con el backend. Verifica que el servicio esté corriendo.")
    st.stop()

# Manejar session_id desde URL o generar nuevo
if "session_id" in st.query_params:
    session_id = st.query_params["session_id"]
else:
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    st.query_params["session_id"] = session_id

st.session_state["session_id"] = session_id

# Manejar user_id
if "user_id" not in st.session_state:
    st.session_state["user_id"] = "USR001"

if "selected_canal_id" not in st.session_state:
    st.session_state["selected_canal_id"] = ""

# Cargar historial si no existe en sesión
if "messages" not in st.session_state or not st.session_state.get("messages_loaded", False):
    with st.spinner("🔄 Recuperando historial de conversación..."):
        try:
            res = requests.get(f"{HISTORY_URL}/{session_id}", timeout=10)
            if res.status_code == 200:
                remote_messages = res.json().get("messages", [])
                if remote_messages:
                    st.session_state["messages"] = remote_messages
                else:
                    st.session_state["messages"] = [
                        {"role": "assistant", "content": "👋 ¡Hola! Soy tu asistente de SolidSET. ¿En qué puedo ayudarte hoy?"}
                    ]
            else:
                st.session_state["messages"] = [
                    {"role": "assistant", "content": "👋 ¡Hola! Soy tu asistente de SolidSET. ¿En qué puedo ayudarte hoy?"}
                ]
            st.session_state["messages_loaded"] = True
        except Exception as e:
            st.warning(f"⚠️ No se pudo cargar el historial remoto: {str(e)}")
            st.session_state["messages"] = [
                {"role": "assistant", "content": "👋 ¡Hola! Soy tu asistente de SolidSET. ¿En qué puedo ayudarte hoy?"}
            ]
            st.session_state["messages_loaded"] = True

# ============================================================
# BARRA LATERAL
# ============================================================

with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/gear.png", width=80)
    st.markdown("### ⚙️ Machining Assistant")
    st.caption("Versión 2.0 - Aprendizaje Contextual")
    
    st.divider()
    
    # --- SELECCIÓN DE USUARIO ---
    st.subheader("👤 Identificación")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        user_id_input = st.text_input(
            "ID del usuario",
            value=st.session_state["user_id"],
            help="Ingresa el ID del recurso humano que está consultando",
            key="user_id_input"
        )
    
    with col2:
        st.write("")
        if st.button("🔄", help="Actualizar contexto del usuario", use_container_width=True):
            if user_id_input != st.session_state["user_id"]:
                st.session_state["user_id"] = user_id_input
                # Limpiar mensajes al cambiar de usuario
                st.session_state["messages"] = [
                    {"role": "assistant", "content": f"👋 Sesión iniciada para usuario **{user_id_input}**. ¿En qué puedo ayudarte?"}
                ]
                st.session_state["messages_loaded"] = True
                # Crear nueva sesión
                new_session = f"session_{uuid.uuid4().hex[:8]}"
                st.query_params["session_id"] = new_session
                st.session_state["session_id"] = new_session
                st.rerun()
    
    # Mostrar información del usuario actual
    if st.session_state.get("user_id"):
        st.markdown(f"""
        <div class="user-badge">
            🧑 <strong>{st.session_state['user_id']}</strong>
        </div>
        """, unsafe_allow_html=True)
    
    st.divider()
    
    # --- CONTEXTO DEL USUARIO ---
    st.subheader("📊 Contexto del Usuario")
    
    if st.button("🔄 Actualizar contexto", use_container_width=True):
        with st.spinner("Consultando contexto del usuario..."):
            contexto = obtener_contexto_usuario(st.session_state["user_id"])
            if contexto:
                st.session_state["user_context"] = contexto
                st.success("✅ Contexto actualizado")
            else:
                st.error("❌ No se pudo obtener el contexto")
    
    if st.session_state.get("user_context"):
        ctx = st.session_state["user_context"]
        ctx_data = ctx.get("context", {})
        
        if ctx_data:
            usuario = ctx_data.get("usuario", {})
            st.markdown(f"""
            **Nombre:** {usuario.get('nombre', 'N/A')}  
            **Rol:** `{usuario.get('rol', 'N/A')}`  
            **Departamento:** {usuario.get('departamento', 'N/A')}  
            **Especialidades:** {', '.join(usuario.get('especialidades', [])) or 'No especificadas'}
            """)
            
            canales = ctx_data.get("canales_acceso", [])
            if canales:
                st.markdown("**Canales de acceso:**")
                for canal in canales[:3]:
                    st.markdown(f"• {canal.get('nombre', 'N/A')} ({canal.get('tipo', 'N/A')})")
                if len(canales) > 3:
                    st.caption(f"... y {len(canales) - 3} canales más")

                canal_options = [("", "(Sin canal específico)")]
                for canal in canales:
                    canal_options.append((canal.get("id", ""), canal.get("nombre", "Canal")))

                canal_labels = [f"{name} | {cid[:8]}" if cid else name for cid, name in canal_options]
                default_index = 0
                for idx, (cid, _) in enumerate(canal_options):
                    if cid and cid == st.session_state.get("selected_canal_id"):
                        default_index = idx
                        break

                selected_label = st.selectbox(
                    "Canal activo para consultas",
                    options=canal_labels,
                    index=default_index,
                    help="Si eliges un canal, las consultas de historial se filtran por ese canal.",
                )

                selected_idx = canal_labels.index(selected_label)
                st.session_state["selected_canal_id"] = canal_options[selected_idx][0] or ""
            
            actividades = ctx_data.get("actividades_recientes", [])
            if actividades:
                st.markdown(f"**Actividades recientes:** {len(actividades)}")
    
    st.divider()
    
    # --- ACCIONES DE LA SESIÓN ---
    st.subheader("🛠️ Acciones")
    
    # Exportar conversación
    if st.button("📥 Exportar conversación (.txt)", use_container_width=True):
        texto_export = generar_texto_conversacion(
            st.session_state["messages"],
            st.session_state["session_id"],
            st.session_state["user_id"]
        )
        st.download_button(
            label="📄 Descargar archivo",
            data=texto_export,
            file_name=f"conversacion_{st.session_state['session_id']}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt",
            mime="text/plain",
            use_container_width=True
        )
    
    # Limpiar sesión
    if st.button("🗑️ Nueva conversación", use_container_width=True):
        limpiar_sesion()
    
    # Eliminar historial en backend
    if st.button("🧹 Limpiar historial remoto", use_container_width=True):
        try:
            res = requests.delete(f"{HISTORY_URL}/{st.session_state['session_id']}", timeout=10)
            if res.status_code == 200:
                st.success("✅ Historial remoto eliminado")
                # Recargar mensajes
                st.session_state["messages"] = [
                    {"role": "assistant", "content": "🧹 Historial limpiado. ¿En qué puedo ayudarte?"}
                ]
                st.session_state["messages_loaded"] = True
                st.rerun()
            else:
                st.error(f"❌ Error: {res.status_code}")
        except Exception as e:
            st.error(f"❌ Error de conexión: {str(e)}")
    
    st.divider()
    
    # --- AUDIO ---
    st.subheader("🔊 Audio")
    
    if st.button("🎙️ Leer última respuesta", use_container_width=True):
        ultimas_respuestas = [m["content"] for m in st.session_state["messages"] if m["role"] == "assistant"]
        if ultimas_respuestas:
            texto_audio = ultimas_respuestas[-1].replace('"', "'").replace('\n', ' ')[:500]
            js_code = f"""
            <script>
                var msg = new SpeechSynthesisUtterance("{texto_audio}");
                msg.lang = 'es-ES';
                msg.rate = 0.9;
                window.speechSynthesis.speak(msg);
            </script>
            """
            st.components.v1.html(js_code, height=0)
            st.success("🔊 Reproduciendo audio...")
        else:
            st.warning("No hay respuestas para reproducir")
    
    st.divider()
    st.caption(f"🆔 Sesión: `{st.session_state['session_id'][:12]}...`")

# ============================================================
# ÁREA PRINCIPAL - CHAT
# ============================================================

st.markdown('<div class="main-header">⚙️ Agente de IA SolidSET</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">SolidSET COMmunicator - Assistente de Aprendizagem Contextual</div>', unsafe_allow_html=True)

# Mostrar mensajes
for message in st.session_state["messages"]:
    with st.chat_message(message["role"]):
        st.write(message["content"])

# --- ENTRADA DE TEXTO ---
if user_input := st.chat_input("Escribe tu consulta o reporte de falla..."):
    # 1. Mostrar mensaje del usuario inmediatamente
    st.session_state["messages"].append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    # 2. Procesar con el backend
    with st.chat_message("assistant"):
        with st.spinner("🧠 Procesando consulta con contexto del usuario..."):
            try:
                payload = {
                    "session_id": st.session_state["session_id"],
                    "message": user_input,
                    "user_id": st.session_state["user_id"],
                    "canal_id": st.session_state.get("selected_canal_id") or None,
                    "generate_audio": False
                }
                
                response = requests.post(
                    DIALOGUE_URL, 
                    json=payload, 
                    timeout=300,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    bot_response = data.get("agent_response", "⚠️ Sin respuesta del agente.")
                    
                    # Mostrar si se usó contexto
                    if data.get("user_context_used"):
                        st.caption(f"📊 Contexto usado: {data['user_context_used']}")
                else:
                    bot_response = f"⚠️ Error en el servidor (Status {response.status_code}). Detalle: {response.text[:200]}"
                    
            except requests.exceptions.Timeout:
                bot_response = "⏰ La consulta está tomando demasiado tiempo. Por favor, intenta con una pregunta más específica."
            except requests.exceptions.ConnectionError:
                bot_response = "🔌 No se pudo conectar con el servidor. Verifica que el backend esté corriendo."
            except Exception as e:
                bot_response = f"❌ Error al conectar con la API: {str(e)[:200]}"

            st.write(bot_response)
            st.session_state["messages"].append({"role": "assistant", "content": bot_response})
            
            # Actualizar el estado de carga
            st.session_state["messages_loaded"] = True
            st.rerun()

# ============================================================
# PIE DE PÁGINA
# ============================================================

st.divider()
col1, col2, col3 = st.columns(3)
with col1:
    st.caption(f"🆔 Sesión: {st.session_state['session_id'][:12]}...")
with col2:
    st.caption(f"👤 Usuario: {st.session_state['user_id']}")
with col3:
    st.caption(f"💬 {len(st.session_state['messages'])} mensajes")

# Actualizar contexto automáticamente cada cierto tiempo (solo si hay cambios)
if st.session_state.get("user_id") and not st.session_state.get("user_context"):
    with st.spinner("Cargando contexto del usuario..."):
        contexto = obtener_contexto_usuario(st.session_state["user_id"])
        if contexto:
            st.session_state["user_context"] = contexto