from langchain_community.chat_message_histories import RedisChatMessageHistory
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import (
    fetch_external_api,
    get_cnc_telemetry,
    learn_new_fact,
    get_db_schema,  # <--- IMPORTAR
    query_sql_server,
    recommend_cnc_action,
)
from app.config import settings
from app.rag.retriever import get_rag_context


class MachiningAgent:

    def __init__(self):
        self.llm = ChatOllama(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.MODEL_NAME,  # Asegúrate de usar 'qwen2.5' o 'llama3.1'
            temperature=0.2,
        )
        self.tools_map = {
            "get_cnc_telemetry": get_cnc_telemetry,
            "recommend_cnc_action": recommend_cnc_action,
            "learn_new_fact": learn_new_fact,
            "query_sql_server": query_sql_server,
            "get_db_schema": get_db_schema, # <--- REGISTRAR
            "fetch_external_api": fetch_external_api,
        }
        self.llm_with_tools = self.llm.bind_tools(list(self.tools_map.values()))

    def analyze_event_with_dialogue(self, session_id: str, user_text: str) -> str:
        if not session_id:
            session_id = "default_session"

        history = RedisChatMessageHistory(session_id, url=settings.REDIS_URL)

        # Detectar si es un saludo simple para omitir consulta pesada al RAG
        clean_text = user_text.strip().lower()
        if clean_text in ["hola", "hola!", "buenos dias", "buenas tardes", "ola", "hello"]:
            rag_context = "Sin contexto adicional requerido para saludos."
        else:
            rag_context = get_rag_context(user_text)

        messages = [
            SystemMessage(
                content=f"{SYSTEM_PROMPT}\n\n[CONTEXTO RAG / BASE CONOCIMIENTO]:\n{rag_context}"
            )
        ]
        
        # Cargar historial reciente
        messages.extend(history.messages[-8:])
        messages.append(HumanMessage(content=user_text))

        # --- BUCLE DE EJECUCIÓN DE HERRAMIENTAS ---
        max_iterations = 5
        iteration = 0
        response_text = ""

        while iteration < max_iterations:
            response = self.llm_with_tools.invoke(messages)
            
            # Verificar si el modelo solicitó ejecutar herramientas
            if hasattr(response, "tool_calls") and response.tool_calls:
                messages.append(response)

                for tool_call in response.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]

                    if tool_name in self.tools_map:
                        try:
                            tool_output = self.tools_map[tool_name].invoke(tool_args)
                        except Exception as err:
                            tool_output = f"Error al ejecutar la herramienta {tool_name}: {str(err)}"

                        messages.append(
                            ToolMessage(
                                content=str(tool_output),
                                tool_call_id=tool_call.get("id", f"call_{iteration}"),
                            )
                        )
                iteration += 1
            else:
                # El modelo entregó la respuesta final al usuario
                response_text = response.content
                break
        else:
            response_text = "No se pudo obtener una respuesta tras ejecutar las herramientas necesarias."

        if not response_text or response_text.strip() == "":
            response_text = "¿En qué te puedo colaborar?"

        # Persistir la conversación
        history.add_user_message(user_text)
        history.add_ai_message(response_text)

        return response_text