import os
import json
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

# URL del backend
BASE_API_URL = os.getenv("API_URL", "http://agent-service:8000/api/v1/agent")
DIALOGUE_URL = f"{BASE_API_URL}/dialogue"

# Inicializar sesión de chat en el navegador
if "session_id" not in st.session_state:
    st.session_state["session_id"] = "session_gui_001"

if "messages" not in st.session_state:
    st.session_state["messages"] = [
        {"role": "assistant", "content": "¡Hola, operario! Soy tu asistente de mecanizado. ¿En qué puedo ayudarte hoy?"}
    ]

# --- 🛠️ BARRA LATERAL: EXPORTACIÓN Y AUDIO ---
st.sidebar.header("🛠️ Acciones de la Sesión")

# 1. EXPORTAR CONVERSACIÓN A TXT
def generar_texto_conversacion():
    texto_export = f"--- HISTORIAL DE CONVERSACIÓN (Sesión: {st.session_state['session_id']}) ---\n\n"
    for msg in st.session_state["messages"]:
        rol = "OPERARIO" if msg["role"] == "user" else "ASISTENTE"
        texto_export += f"[{rol}]: {msg['content']}\n\n"
    return texto_export

st.sidebar.download_button(
    label="📥 Exportar conversación (.txt)",
    data=generar_texto_conversacion(),
    file_name=f"conversacion_{st.session_state['session_id']}.txt",
    mime="text/plain",
    use_container_width=True
)

# 2. REPRODUCIR / GENERAR AUDIO DE LA ÚLTIMA RESPUESTA O HISTORIAL
st.sidebar.subheader("🔊 Audio")

# Opción A: Reproducción por navegador mediante HTML/JS (Rápido y sin coste)
if st.sidebar.button("🎙️ Leer última respuesta (Navegador)", use_container_width=True):
    ultimas_respuestas = [m["content"] for m in st.session_state["messages"] if m["role"] == "assistant"]
    if ultimas_respuestas:
        texto_audio = ultimas_respuestas[-1].replace('"', "'").replace('\n', ' ')
        # Inyección de código JavaScript SpeechSynthesis
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

# Opción B: Generar audio vía Backend (FastAPI endpoint /audio-response)
if st.sidebar.button("🎵 Generar Audio MP3 (Backend)", use_container_width=True):
    ultimas_respuestas = [m["content"] for m in st.session_state["messages"] if m["role"] == "assistant"]
    if ultimas_respuestas:
        with st.spinner("Generando archivo de audio desde el servidor..."):
            try:
                # Se envía solicitud con generate_audio = True al endpoint existente en FastAPI
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

# --- RENDERIZADO DEL CHAT ---
for message in st.session_state["messages"]:
    with st.chat_message(message["role"]):
        st.write(message["content"])

# --- ENTRADA DE TEXTO DEL USUARIO ---
if user_input := st.chat_input("Escribe tu consulta o reporte de falla..."):
    # 1. Mostrar mensaje del operario
    st.session_state["messages"].append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    # 2. Consultar a la API de FastAPI
    with st.chat_message("assistant"):
        with st.spinner("Procesando telemetría y RAG..."):
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

            # Mostrar la respuesta del agente y guardar en el historial
            st.write(bot_response)
            st.session_state["messages"].append({"role": "assistant", "content": bot_response})