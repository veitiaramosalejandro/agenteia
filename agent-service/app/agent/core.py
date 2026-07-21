from langchain_community.chat_message_histories import RedisChatMessageHistory
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import (
    fetch_external_api,
    get_cnc_telemetry,
    learn_new_fact,
    query_sql_server,
    recommend_cnc_action,
)
from app.config import settings
from app.rag.retriever import get_rag_context


class MachiningAgent:

    def __init__(self):
        self.llm = ChatOllama(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.MODEL_NAME,
            temperature=0.2,
        )
        self.tools_map = {
            "get_cnc_telemetry": get_cnc_telemetry,
            "recommend_cnc_action": recommend_cnc_action,
            "learn_new_fact": learn_new_fact,
            "query_sql_server": query_sql_server,
            "fetch_external_api": fetch_external_api,
        }
        self.llm_with_tools = self.llm.bind_tools(list(self.tools_map.values()))

    def analyze_event_with_dialogue(
        self, session_id: str, user_text: str
    ) -> str:
        if not session_id:
            session_id = "default_session"

        history = RedisChatMessageHistory(session_id, url=settings.REDIS_URL)
        rag_context = get_rag_context(user_text)

        # Incrementamos el historial a los últimos 10 mensajes para no perder el contexto de preguntas anteriores
        messages = [
            SystemMessage(
                content=f"{SYSTEM_PROMPT}\n\n[CONTEXTO RAG / BASE CONOCIMIENTO]:\n{rag_context}"
            )
        ]
        messages.extend(history.messages[-10:])
        messages.append(HumanMessage(content=user_text))

        # 1. Invocación inicial
        response = self.llm_with_tools.invoke(messages)

        # 2. Si el modelo solicita ejecutar una herramienta
        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)

            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                if tool_name in self.tools_map:
                    # Ejecutar herramienta con manejo seguro de errores si SQL Server no está disponible
                    try:
                        tool_output = self.tools_map[tool_name].invoke(
                            tool_args
                        )
                    except Exception as err:
                        tool_output = f"Error al ejecutar la herramienta {tool_name}: {str(err)}"

                    messages.append(
                        ToolMessage(
                            content=str(tool_output),
                            tool_call_id=tool_call.get("id", "call_default"),
                        )
                    )

            # Segunda invocación para respuesta final en lenguaje natural
            final_response = self.llm_with_tools.invoke(messages)
            response_text = final_response.content
        else:
            response_text = response.content

        # Si por alguna razón la respuesta quedó vacía o falló la generación
        if not response_text or response_text.strip() == "":
            response_text = (
                "Entendido. ¿Deseas que revise la telemetría o algún otro dato?"
            )

        # Guardar interacción en Redis
        history.add_user_message(user_text)
        history.add_ai_message(response_text)

        return response_text