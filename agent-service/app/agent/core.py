from langchain_community.chat_message_histories import RedisChatMessageHistory
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import (
    fetch_external_api,
    get_cnc_telemetry,
    learn_new_fact,
    get_db_schema,
    query_sql_server,
    recommend_cnc_action,
    confirm_large_operation,  # 🚨 NUEVA
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
            "get_db_schema": get_db_schema,
            "fetch_external_api": fetch_external_api,
            "confirm_large_operation": confirm_large_operation,  # 🚨 REGISTRAR
        }
        self.llm_with_tools = self.llm.bind_tools(list(self.tools_map.values()))

    def _summarize_conversation(self, history_messages, session_id: str) -> str:
        """Resume la conversación si hay más de 15 mensajes para no perder contexto."""
        if len(history_messages) <= 15:
            return None
        
        # Tomar los primeros 3 y los últimos 5 para contexto
        to_summarize = history_messages[3:-5]
        if not to_summarize:
            return None
        
        summary_prompt = f"""
        Resume la siguiente conversación entre un operario de CNC y un asistente técnico.
        Extrae SOLO los puntos clave: problemas reportados, diagnósticos dados, acciones tomadas.
        Sé conciso, máximo 5 líneas.
        
        Conversación:
        {[f"{m.type}: {m.content}" for m in to_summarize]}
        """
        
        try:
            summary_response = self.llm.invoke([HumanMessage(content=summary_prompt)])
            return f"[RESUMEN DE CONVERSACIÓN ANTERIOR]: {summary_response.content}"
        except:
            return None

    def analyze_event_with_dialogue(self, session_id: str, user_text: str) -> str:
        if not session_id:
            session_id = "default_session"

        history = RedisChatMessageHistory(session_id, url=settings.REDIS_URL)

        # 🚨 MEJORA: Detectar saludos simples (ya lo tenías, lo mantengo)
        clean_text = user_text.strip().lower()
        saludos = ["hola", "hola!", "buenos dias", "buenas tardes", "ola", "hello", "hi", "hey"]
        if clean_text in saludos:
            rag_context = "Sin contexto adicional requerido para saludos."
        else:
            rag_context = get_rag_context(user_text)

        # 🚨 MEJORA: Construir mensajes con mejor manejo de idioma
        system_msg = SystemMessage(content=SYSTEM_PROMPT)
        
        # El contexto RAG va como mensaje separado (NO dentro del System Prompt)
        rag_msg = HumanMessage(
            content=f"📚 CONTEXTO TÉCNICO RELEVANTE (para usar si es necesario, respeta el idioma del usuario):\n{rag_context}"
        )
        
        messages = [system_msg, rag_msg]
        
        # 🚨 MEJORA: Resumir historial si es muy largo
        all_history = list(history.messages)
        summary = self._summarize_conversation(all_history, session_id)
        
        if summary:
            # Guardar el resumen como un mensaje del sistema en el historial actual
            messages.append(SystemMessage(content=summary))
            # Solo cargar los últimos 5 mensajes después del resumen
            messages.extend(all_history[-5:])
        else:
            # Cargar últimos 8 mensajes (comportamiento normal)
            messages.extend(all_history[-8:])
        
        messages.append(HumanMessage(content=user_text))

        # --- BUCLE DE EJECUCIÓN DE HERRAMIENTAS ---
        max_iterations = 5
        iteration = 0
        response_text = ""

        while iteration < max_iterations:
            response = self.llm_with_tools.invoke(messages)
            
            if hasattr(response, "tool_calls") and response.tool_calls:
                messages.append(response)

                for tool_call in response.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]

                    # 🚨 MEJORA: Si es confirm_large_operation, el agente espera la respuesta del usuario
                    if tool_name == "confirm_large_operation":
                        # La herramienta devuelve un mensaje pidiendo confirmación
                        confirm_msg = self.tools_map[tool_name].invoke(tool_args)
                        messages.append(
                            ToolMessage(
                                content=str(confirm_msg),
                                tool_call_id=tool_call.get("id", f"call_{iteration}"),
                            )
                        )
                        # Pedir al modelo que espere confirmación
                        wait_prompt = HumanMessage(
                            content="He solicitado confirmación al usuario. Espera su respuesta (Sí/No) antes de continuar."
                        )
                        messages.append(wait_prompt)
                    elif tool_name in self.tools_map:
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