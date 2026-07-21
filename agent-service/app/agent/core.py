from langchain_community.chat_message_histories import RedisChatMessageHistory
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from langchain_ollama import ChatOllama

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import (
    get_cnc_telemetry,
    learn_new_fact,
    recommend_cnc_action,
    query_sql_server,
    fetch_external_api
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
            "fetch_external_api": fetch_external_api
        }
        self.llm_with_tools = self.llm.bind_tools(list(self.tools_map.values()))

    def analyze_event_with_dialogue(
        self, session_id: str, user_text: str
    ) -> str:
        if not session_id:
            session_id = "default_session"

        history = RedisChatMessageHistory(session_id, url=settings.REDIS_URL)
        rag_context = get_rag_context(user_text)

        messages = [
            SystemMessage(
                content=f"{SYSTEM_PROMPT}\n\n[CONTEXTO RAG]:\n{rag_context}"
            )
        ]
        messages.extend(history.messages[-6:])
        messages.append(HumanMessage(content=user_text))

        # 1. Invocación inicial
        response = self.llm_with_tools.invoke(messages)

        # 2. Si el modelo solicita ejecutar una herramienta (Tool Calling nativo)
        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)

            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                if tool_name in self.tools_map:
                    tool_output = self.tools_map[tool_name].invoke(tool_args)

                    messages.append(
                        ToolMessage(
                            content=str(tool_output),
                            tool_call_id=tool_call.get("id", "call_default"),
                        )
                    )

            # Segunda invocación para que el LLM redacte la respuesta final
            final_response = self.llm_with_tools.invoke(messages)
            response_text = final_response.content
        else:
            response_text = response.content

        # 3. Limpieza de respuestas si el LLM emitió un JSON de tool_call en texto plano
        if response_text and ('{"name": "fetch_external_api"' in response_text or '{"name":' in response_text):
            response_text = "¡Hola! Soy tu asistente de mecanizado. ¿En qué puedo ayudarte hoy con la máquina o la integración de datos?"

        if not response_text:
            response_text = "Procesado correctamente. ¿En qué más puedo ayudarte?"

        # Persistencia en Redis
        history.add_user_message(user_text)
        history.add_ai_message(response_text)

        return response_text