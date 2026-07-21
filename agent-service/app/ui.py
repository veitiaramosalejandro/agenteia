import streamlit as st
import requests
import os

# Configuración de la ventana
st.set_page_config(
    page_title="Machining Assistant AI",
    page_icon="⚙️",
    layout="centered"
)

st.title("⚙️ Machining Assistant AI")
st.caption("Asistente experto para diagnóstico de maquinaria CNC HARTFORD")

# URL del backend (Usa localhost si ejecutas streamlit fuera de Docker, o agent-service si usas docker-compose)
API_URL = os.getenv("API_URL", "http://agent-service:8000/api/v1/agent/dialogue")

# Inicializar sesión de chat en el navegador
if "session_id" not in st.session_state:
    st.session_state["session_id"] = "session_gui_001"

if "messages" not in st.session_state:
    st.session_state["messages"] = [
        {"role": "assistant", "content": "¡Hola, operario! Soy tu asistente de mecanizado. ¿En qué puedo ayudarte hoy?"}
    ]

# Renderizar el historial de conversación en pantalla
for message in st.session_state["messages"]:
    with st.chat_message(message["role"]):
        st.write(message["content"])

# Entrada de texto del usuario
if user_input := st.chat_input("Escribe tu consulta o reporte de falla..."):
    # 1. Mostrar mensaje del operario
    st.session_state["messages"].append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    # 2. Consultar a la API de FastAPI
    with st.chat_message("assistant"):
        with st.spinner("Procesando telemetría y RAG..."):
            try:
                # Estructura del JSON corregida para FastAPI
                payload = {
                    "session_id": st.session_state["session_id"],
                    "message": user_input
                }
                response = requests.post(API_URL, json=payload, timeout=300)
                
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