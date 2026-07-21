from langchain_community.chat_message_histories import RedisChatMessageHistory
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from langchain_ollama import ChatOllama

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import get_cnc_telemetry, recommend_cnc_action, learn_new_fact
from app.config import settings
from app.rag.retriever import get_rag_context


class MachiningAgent:

    def __init__(self):
        self.llm = ChatOllama(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.MODEL_NAME,
            temperature=0.2,
        )
        # Diccionario de herramientas para ejecucion rápida
        self.tools_map = {
            "get_cnc_telemetry": get_cnc_telemetry,
            "recommend_cnc_action": recommend_cnc_action,
            "learn_new_fact": learn_new_fact,
        }
        self.llm_with_tools = self.llm.bind_tools(list(self.tools_map.values()))

    def analyze_event_with_dialogue(self, session_id: str, user_text: str) -> str:
        # Generar un ID predeterminado si el usuario envía un session_id vacío
        if not session_id:
            session_id = "default_session"

        history = RedisChatMessageHistory(session_id, url=settings.REDIS_URL)
        rag_context = get_rag_context(user_text)

        messages = [
            SystemMessage(
                content=f"{SYSTEM_PROMPT}\n\n[CONTEXTO TÉCNICO Y MANUALES RAG]:\n{rag_context}"
            )
        ]

        # Añadir historial reciente
        messages.extend(history.messages[-6:])
        messages.append(HumanMessage(content=user_text))

        # 1. Primera invocación al modelo
        response = self.llm_with_tools.invoke(messages)

        # 2. Si el modelo quiere ejecutar herramientas, las ejecutamos y hacemos una 2da invocación
        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)  # Guardar la decisión del modelo

            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                if tool_name in self.tools_map:
                    # Ejecutar la herramienta correspondiente
                    tool_output = self.tools_map[tool_name].invoke(tool_args)

                    # Devolver el resultado de la herramienta al modelo
                    messages.append(
                        ToolMessage(
                            content=str(tool_output),
                            tool_call_id=tool_call["id"],
                        )
                    )

            # 3. Segunda invocación para que el LLM procese la respuesta de la herramienta
            final_response = self.llm_with_tools.invoke(messages)
            response_text = final_response.content
        else:
            response_text = response.content

        # Fallback en caso de que aún así no genere texto
        if not response_text:
            response_text = "Procesado correctamente. ¿En qué más puedo ayudarte con la máquina?"

        # Persistir en historial
        history.add_user_message(user_text)
        history.add_ai_message(response_text)

        return response_text