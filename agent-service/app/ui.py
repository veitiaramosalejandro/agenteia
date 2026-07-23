import os
import requests
import streamlit as st

# Configuración de la ventana
st.set_page_config(
    page_title="Machining Assistant AI",
    page_icon="⚙️",
    layout="centered"
)

st.title("⚙️ Machining Assistant AI")
st.caption("Asistente experto para diagnóstico de maquinaria CNC HARTFORD")

# --- CONFIGURACIÓN DE URLS Y SESIÓN ---
BASE_API_URL = os.getenv("API_URL", "http://agent-service:8000/api/v1/agent")

if BASE_API_URL.endswith("/"):
    BASE_API_URL = BASE_API_URL[:-1]
if BASE_API_URL.endswith("/dialogue"):
    BASE_API_URL = BASE_API_URL.replace("/dialogue", "")

DIALOGUE_URL = f"{BASE_API_URL}/dialogue"
HISTORY_URL = f"{BASE_API_URL}/history"

# 1. MANTENER SESSION_ID EN LA URL PARA RESISTIR RECARGAS (F5)
if "session_id" in st.query_params:
    session_id = st.query_params["session_id"]
else:
    session_id = "session_gui_001"
    st.query_params["session_id"] = session_id

st.session_state["session_id"] = session_id

# 2. CARGAR EL HISTORIAL DESDE REDIS AL INICIALIZAR O RECARGAR
if "messages" not in st.session_state:
    with st.spinner("Recuperando historial de conversación..."):
        try:
            res = requests.get(f"{HISTORY_URL}/{session_id}", timeout=10)
            if res.status_code == 200:
                remote_messages = res.json().get("messages", [])
                if remote_messages:
                    st.session_state["messages"] = remote_messages
                else:
                    st.session_state["messages"] = [
                        {"role": "assistant", "content": "¡Hola, operario! Soy tu asistente de mecanizado. ¿En qué puedo ayudarte hoy?"}
                    ]
            else:
                st.session_state["messages"] = [
                    {"role": "assistant", "content": "¡Hola, operario! Soy tu asistente de mecanizado. ¿En qué puedo ayudarte hoy?"}
                ]
        except Exception:
            st.session_state["messages"] = [
                {"role": "assistant", "content": "¡Hola, operario! Soy tu asistente de mecanizado. ¿En qué puedo ayudarte hoy?"}
            ]

# --- 🛠️ BARRA LATERAL: EXPORTAR, AUDIO Y SESIÓN ---
st.sidebar.header("🛠️ Acciones de la Sesión")
st.sidebar.caption(f"ID Sesión: `{session_id}`")

# 1. EXPORTAR CONVERSACIÓN A TXT
def generar_texto_conversacion():
    texto_export = f"--- HISTORIAL DE CONVERSACIÓN (Sesión: {session_id}) ---\n\n"
    for msg in st.session_state["messages"]:
        rol = "OPERARIO" if msg["role"] == "user" else "ASISTENTE"
        texto_export += f"[{rol}]: {msg['content']}\n\n"
    return texto_export

st.sidebar.download_button(
    label="📥 Exportar conversación (.txt)",
    data=generar_texto_conversacion(),
    file_name=f"conversacion_{session_id}.txt",
    mime="text/plain",
    use_container_width=True
)

# 2. REPRODUCIR / GENERAR AUDIO DE LA ÚLTIMA RESPUESTA
st.sidebar.subheader("🔊 Audio")

# Opción A: Reproducción por navegador mediante HTML/JS
if st.sidebar.button("🎙️ Leer última respuesta (Navegador)", use_container_width=True):
    ultimas_respuestas = [m["content"] for m in st.session_state["messages"] if m["role"] == "assistant"]
    if ultimas_respuestas:
        texto_audio = ultimas_respuestas[-1].replace('"', "'").replace('\n', ' ')
        js_code = f"""
        <script>
            var msg = new SpeechSynthesisUtterance("{texto_audio}");
            msg.lang = 'es-ES';
            window.speechSynthesis.speak(msg);
        </script>
        """
        st.components.v1.html(js_code, height=0)
    else:
        st.sidebar.warning("No hay respuestas registradas para reproducir.")

# Opción B: Generar audio MP3 vía Backend (FastAPI endpoint)
if st.sidebar.button("🎵 Generar Audio MP3 (Backend)", use_container_width=True):
    ultimas_respuestas = [m["content"] for m in st.session_state["messages"] if m["role"] == "assistant"]
    if ultimas_respuestas:
        with st.spinner("Generando archivo de audio desde el servidor..."):
            try:
                payload = {
                    "session_id": st.session_state["session_id"],
                    "message": "Generar audio de contexto",
                    "generate_audio": True
                }
                res = requests.post(DIALOGUE_URL, json=payload, timeout=60)
                if res.status_code == 200 and "audio_url" in res.json():
                    audio_endpoint = f"{BASE_API_URL}/audio-response?file=" + res.json()["audio_url"].split("file=")[-1]
                    audio_res = requests.get(audio_endpoint)
                    if audio_res.status_code == 200:
                        st.sidebar.audio(audio_res.content, format="audio/mp3")
                    else:
                        st.sidebar.error("Error al obtener el archivo de audio.")
                else:
                    st.sidebar.error("El backend no devolvió una URL de audio válida.")
            except Exception as e:
                st.sidebar.error(f"Error de conexión: {str(e)}")

st.sidebar.divider()

# 3. REINICIAR CONVERSACIÓN
if st.sidebar.button("🗑️ Nueva Conversación / Limpiar", use_container_width=True):
    import uuid
    new_session = f"session_{uuid.uuid4().hex[:8]}"
    st.query_params["session_id"] = new_session
    st.session_state["session_id"] = new_session
    st.session_state["messages"] = [
        {"role": "assistant", "content": "¡Hola, operario! Nueva sesión iniciada. ¿En qué te puedo ayudar?"}
    ]
    st.rerun()

# --- RENDERIZADO DEL CHAT ---
for message in st.session_state["messages"]:
    with st.chat_message(message["role"]):
        st.write(message["content"])

# --- ENTRADA DE TEXTO ---
if user_input := st.chat_input("Escribe tu consulta o reporte de falla..."):
    # 1. Mostrar mensaje inmediatamente
    st.session_state["messages"].append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    # 2. Enviar a FastAPI
    with st.chat_message("assistant"):
        with st.spinner("Procesando consulta..."):
            try:
                payload = {
                    "session_id": st.session_state["session_id"],
                    "message": user_input,
                    "generate_audio": False
                }
                response = requests.post(DIALOGUE_URL, json=payload, timeout=300)
                
                if response.status_code == 200:
                    data = response.json()
                    bot_response = data.get("agent_response", "Sin respuesta del agente.")
                else:
                    bot_response = f"⚠️ Error en el servidor (Status {response.status_code}). Detalle: {response.text}"
            except Exception as e:
                bot_response = f"❌ Error al conectar con la API: {str(e)}"

            st.write(bot_response)
            st.session_state["messages"].append({"role": "assistant", "content": bot_response})