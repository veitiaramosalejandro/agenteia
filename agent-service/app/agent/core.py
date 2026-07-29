import hashlib
from typing import Optional, List, Dict, Any
from datetime import datetime

from langchain_community.chat_message_histories import RedisChatMessageHistory
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, AIMessage
from langchain_ollama import ChatOllama

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import (
    fetch_external_api,
    get_cnc_telemetry,
    learn_new_fact,
    get_db_schema,
    query_sql_server,
    recommend_cnc_action,
    confirm_large_operation,
    analyze_pcm_audio_diagnostic,
)
from app.config import settings
from app.rag.retriever import get_rag_context
from app.system.learning import SistemaAprendizaje


class MachiningAgent:
    """
    Agente inteligente para diagnóstico de maquinaria CNC con:
    - Sistema de aprendizaje contextual (canales, recursos, roles)
    - Memoria a corto plazo (Redis)
    - Memoria a largo plazo (Qdrant/RAG)
    - Herramientas especializadas (SQL, APIs, telemetría)
    - Sistema de confirmación (Human-in-the-loop)
    - Resumidor automático de conversaciones largas
    """
    
    def __init__(self):
        # Configuración del LLM
        self.llm = ChatOllama(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.MODEL_NAME,
            temperature=0.2,
            num_predict=2048,  # Máximo de tokens a generar
            top_p=0.9,
            repeat_penalty=1.1,
        )
        
        # Mapa de herramientas disponibles
        self.tools_map = {
            "get_cnc_telemetry": get_cnc_telemetry,
            "recommend_cnc_action": recommend_cnc_action,
            "learn_new_fact": learn_new_fact,
            "query_sql_server": query_sql_server,
            "get_db_schema": get_db_schema,
            "fetch_external_api": fetch_external_api,
            "confirm_large_operation": confirm_large_operation,
            "analyze_pcm_audio_diagnostic": analyze_pcm_audio_diagnostic,
        }
        
        # Vincular herramientas al LLM
        self.llm_with_tools = self.llm.bind_tools(list(self.tools_map.values()))
        
        # Sistema de aprendizaje contextual
        self.sistema_aprendizaje = SistemaAprendizaje()
        
        # Configuración de memoria
        self.max_history_messages = 12  # Máximo de mensajes a mantener sin resumir
        self.max_iterations = 5  # Máximo de iteraciones en el bucle de herramientas
        
        # Cache de contextos de usuario (para evitar consultas repetidas)
        self.user_context_cache = {}
        self.cache_ttl = 300  # 5 minutos

    # ============================================================
    # 1. GESTIÓN DE CONTEXTO DE USUARIO
    # ============================================================
    
    def _get_user_context(self, user_id: str) -> str:
        """
        Obtiene el contexto del usuario con caché para optimizar rendimiento.
        """
        if not user_id:
            return ""
        
        # Verificar caché
        cache_key = f"user_context_{user_id}"
        if cache_key in self.user_context_cache:
            cached_data, timestamp = self.user_context_cache[cache_key]
            if (datetime.now() - timestamp).seconds < self.cache_ttl:
                return cached_data
        
        # Obtener contexto fresco
        try:
            contexto = self.sistema_aprendizaje.generar_contexto_agente(user_id)
            if contexto:
                # Guardar en caché
                self.user_context_cache[cache_key] = (contexto, datetime.now())
                return contexto
        except Exception as e:
            print(f"⚠️ Error obteniendo contexto del usuario {user_id}: {e}")
        
        return ""

    def _get_aprendizaje_relevante(self, query: str, user_id: str) -> str:
        """
        Consulta el aprendizaje relevante para la consulta del usuario.
        """
        try:
            # Obtener contexto del usuario para filtrar por canales
            contexto_obj = self.sistema_aprendizaje.obtener_contexto_usuario(user_id)
            if contexto_obj and contexto_obj.canales_acceso:
                # Buscar en todos los canales del usuario
                resultados = []
                for canal in contexto_obj.canales_acceso[:3]:  # Limitar a 3 canales para no saturar
                    aprendizaje = self.sistema_aprendizaje.consultar_aprendizaje(
                        query=query,
                        canal_id=canal.id,
                        limit=2
                    )
                    if aprendizaje and "No hay conocimiento" not in aprendizaje:
                        resultados.append(f"[Canal: {canal.nombre}]\n{aprendizaje}")
                
                if resultados:
                    return "\n\n".join(resultados[:3])
            
            # Si no hay contexto o canales, búsqueda general
            aprendizaje = self.sistema_aprendizaje.consultar_aprendizaje(
                query=query,
                canal_id=None,
                limit=3
            )
            return aprendizaje
            
        except Exception as e:
            print(f"⚠️ Error consultando aprendizaje: {e}")
            return ""

    # ============================================================
    # 2. RESUMEN DE CONVERSACIONES
    # ============================================================
    
    def _should_summarize(self, history_messages: list) -> bool:
        """
        Determina si es necesario resumir la conversación.
        """
        # Si hay más de 15 mensajes, es momento de resumir
        if len(history_messages) > 15:
            return True
        
        # Si la conversación tiene más de 2000 tokens aproximados
        total_chars = sum(len(msg.content) for msg in history_messages if hasattr(msg, 'content'))
        if total_chars > 4000:  # Aproximadamente 1000 tokens
            return True
        
        return False

    def _summarize_conversation(self, history_messages: list, session_id: str) -> Optional[str]:
        """
        Genera un resumen de la conversación para mantener contexto en conversaciones largas.
        """
        if not self._should_summarize(history_messages):
            return None
        
        # Seleccionar mensajes para resumir (excluir los últimos 5)
        to_summarize = history_messages[:-5] if len(history_messages) > 5 else history_messages
        
        if not to_summarize:
            return None
        
        # Construir texto de la conversación
        conversation_text = ""
        for msg in to_summarize:
            role = "Operario" if msg.type in ["human", "user"] else "Asistente"
            content = msg.content[:500]  # Limitar longitud por mensaje
            conversation_text += f"{role}: {content}\n\n"
        
        # Prompt de resumen
        summary_prompt = f"""
        Eres un asistente que resume conversaciones técnicas sobre maquinaria CNC.
        
        Resumen la siguiente conversación entre un operario y un asistente técnico.
        Extrae SOLO los puntos clave:
        - Problemas o incidentes reportados
        - Diagnósticos realizados
        - Acciones tomadas o recomendadas
        - Decisiones importantes
        
        Sé conciso, máximo 8 líneas.
        Mantén el formato de resumen ejecutivo.
        
        CONVERSACIÓN A RESUMIR:
        {conversation_text[:3000]}
        
        RESUMEN:
        """
        
        try:
            summary_response = self.llm.invoke([HumanMessage(content=summary_prompt)])
            summary = f"[RESUMEN DE CONVERSACIÓN ANTERIOR]: {summary_response.content}"
            
            # Guardar el resumen en Redis como un mensaje del sistema
            history = RedisChatMessageHistory(session_id, url=settings.REDIS_URL)
            history.add_message(SystemMessage(content=summary))
            
            return summary
        except Exception as e:
            print(f"⚠️ Error generando resumen: {e}")
            return None

    # ============================================================
    # 3. FILTROS DE SEGURIDAD ADICIONALES
    # ============================================================
    
    def _validate_user_query(self, user_text: str) -> tuple[bool, str]:
        """
        Valida la consulta del usuario antes de procesarla.
        Retorna (es_valida, mensaje_error)
        """
        # Verificar largo
        if len(user_text) > 5000:
            return False, "La consulta es demasiado larga. Por favor, reduce tu mensaje."
        
        if len(user_text) < 2:
            return False, "Por favor, escribe un mensaje más completo para poder ayudarte."
        
        # Verificar caracteres sospechosos
        caracteres_peligrosos = ['\x00', '\x01', '\x02', '\x03', '\x04']
        for char in caracteres_peligrosos:
            if char in user_text:
                return False, "La consulta contiene caracteres no válidos."
        
        return True, ""

    # ============================================================
    # 4. REGISTRO DE ACTIVIDADES PARA APRENDIZAJE
    # ============================================================
    
    def _registrar_interaccion(self, user_id: str, canal_id: Optional[str], 
                               user_text: str, response_text: str, 
                               herramientas_usadas: List[str]):
        """
        Registra la interacción para que el sistema aprenda de ella.
        """
        try:
            # Determinar el tipo de actividad basado en la consulta
            tipo_actividad = "consulta_general"
            if "alarma" in user_text.lower() or "error" in user_text.lower():
                tipo_actividad = "reporte_incidencia"
            elif "mantenimiento" in user_text.lower() or "reparar" in user_text.lower():
                tipo_actividad = "mantenimiento"
            elif "rendimiento" in user_text.lower() or "producción" in user_text.lower():
                tipo_actividad = "analisis_rendimiento"
            elif "aprende" in user_text.lower() or "enseña" in user_text.lower():
                tipo_actividad = "aprendizaje"
            elif herramientas_usadas and "query_sql_server" in herramientas_usadas:
                tipo_actividad = "consulta_datos"
            elif herramientas_usadas and "get_cnc_telemetry" in herramientas_usadas:
                tipo_actividad = "diagnostico_telemetria"
            
            # Construir descripción enriquecida
            descripcion = f"""
            Consulta del operario: {user_text[:200]}
            Herramientas utilizadas: {', '.join(herramientas_usadas) if herramientas_usadas else 'ninguna'}
            Respuesta generada: {response_text[:200]}
            """
            
            # Registrar actividad
            from app.system.schema import Actividad
            actividad = Actividad(
                id=f"interaccion_{datetime.now().timestamp()}",
                recurso_humano_id=user_id,
                canal_id=canal_id or "canal_general",
                tipo=tipo_actividad,
                descripcion=descripcion,
                timestamp=datetime.now(),
                metadatos={
                    "herramientas_usadas": herramientas_usadas,
                    "longitud_consulta": len(user_text),
                    "longitud_respuesta": len(response_text)
                }
            )
            
            self.sistema_aprendizaje.aprender_actividad(actividad)
            
        except Exception as e:
            print(f"⚠️ Error registrando interacción: {e}")

    # ============================================================
    # 5. MÉTODO PRINCIPAL DE PROCESAMIENTO
    # ============================================================
    
    def analyze_event_with_dialogue(
        self, 
        session_id: str, 
        user_text: str, 
        user_id: Optional[str] = None,
        canal_id: Optional[str] = None
    ) -> str:
        """
        Procesa la consulta del usuario con contexto completo.
        
        Args:
            session_id: ID de la sesión de conversación
            user_text: Mensaje del usuario
            user_id: ID del recurso humano (para contexto personalizado)
            canal_id: ID del canal específico (opcional)
        
        Returns:
            str: Respuesta del agente
        """
        # --- 1. VALIDACIONES INICIALES ---
        is_valid, error_msg = self._validate_user_query(user_text)
        if not is_valid:
            return f"⚠️ {error_msg}"
        
        if not session_id:
            session_id = f"session_{hashlib.md5(user_text.encode()).hexdigest()[:8]}"

        # --- 2. INICIALIZAR MEMORIA ---
        try:
            history = RedisChatMessageHistory(session_id, url=settings.REDIS_URL)
        except Exception as e:
            print(f"❌ Error conectando a Redis: {e}")
            history = None

        # --- 3. DETECTAR SALUDOS ---
        clean_text = user_text.strip().lower()
        saludos = ["hola", "hola!", "buenos dias", "buenas tardes", "ola", "hello", "hi", "hey", "buenas"]
        
        if clean_text in saludos:
            return self._handle_greeting(user_id)

        # --- 4. OBTENER CONTEXTOS ---
        
        # 4.1 Contexto del usuario (canales, rol, permisos)
        contexto_usuario = ""
        if user_id:
            contexto_usuario = self._get_user_context(user_id)
        
        # 4.2 Contexto RAG (documentos técnicos)
        rag_context = get_rag_context(user_text)
        
        # 4.3 Aprendizaje relevante (actividades pasadas similares)
        aprendizaje_relevante = ""
        if user_id:
            aprendizaje_relevante = self._get_aprendizaje_relevante(user_text, user_id)

        # --- 5. CONSTRUIR MENSAJES ---
        
        # System Prompt con contexto del usuario
        system_prompt = SYSTEM_PROMPT
        
        if contexto_usuario:
            system_prompt += f"\n\n=== CONTEXTO DEL USUARIO ({user_id}) ===\n{contexto_usuario}"
        
        if canal_id:
            system_prompt += f"\n\n=== CANAL ACTUAL ===\nID: {canal_id}\nEnfoca tus respuestas en el contexto de este canal."
        
        system_msg = SystemMessage(content=system_prompt)
        
        # Mensaje de contexto RAG
        rag_msg = HumanMessage(
            content=f"📚 DOCUMENTACIÓN TÉCNICA RELEVANTE:\n{rag_context if rag_context else 'No hay documentación específica para esta consulta.'}"
        )
        
        messages = [system_msg, rag_msg]
        
        # Añadir aprendizaje relevante si existe
        if aprendizaje_relevante and "No hay conocimiento" not in aprendizaje_relevante:
            aprendizaje_msg = HumanMessage(
                content=f"🧠 CONOCIMIENTO APRENDIDO DE ACTIVIDADES PREVIAS:\n{aprendizaje_relevante}"
            )
            messages.append(aprendizaje_msg)

        # --- 6. CARGAR HISTORIAL CON RESUMEN ---
        if history:
            all_history = list(history.messages)
            
            # Si hay muchos mensajes, resumir
            if self._should_summarize(all_history):
                summary = self._summarize_conversation(all_history, session_id)
                if summary:
                    messages.append(SystemMessage(content=summary))
                    # Cargar solo últimos 5 mensajes después del resumen
                    messages.extend(all_history[-5:])
                else:
                    messages.extend(all_history[-self.max_history_messages:])
            else:
                messages.extend(all_history[-self.max_history_messages:])
        
        # Añadir mensaje del usuario
        messages.append(HumanMessage(content=user_text))

        # --- 7. BUCLE DE EJECUCIÓN DE HERRAMIENTAS ---
        iteration = 0
        response_text = ""
        herramientas_usadas = []
        last_tool_result = None
        
        while iteration < self.max_iterations:
            try:
                response = self.llm_with_tools.invoke(messages)
            except Exception as e:
                print(f"❌ Error invocando LLM: {e}")
                return f"⚠️ Error procesando la consulta: {str(e)[:100]}"
            
            # Verificar si el modelo solicitó ejecutar herramientas
            if hasattr(response, "tool_calls") and response.tool_calls:
                messages.append(response)
                
                for tool_call in response.tool_calls:
                    tool_name = tool_call.get("name", "")
                    tool_args = tool_call.get("args", {})
                    
                    print(f"🔧 Ejecutando herramienta: {tool_name} con args: {tool_args}")
                    
                    # Herramienta de confirmación (Human-in-the-loop)
                    if tool_name == "confirm_large_operation":
                        try:
                            confirm_msg = self.tools_map[tool_name].invoke(tool_args)
                            messages.append(
                                ToolMessage(
                                    content=str(confirm_msg),
                                    tool_call_id=tool_call.get("id", f"call_{iteration}"),
                                )
                            )
                            # Esperar confirmación
                            wait_prompt = HumanMessage(
                                content="He solicitado confirmación al usuario. Espera su respuesta (Sí/No) antes de continuar."
                            )
                            messages.append(wait_prompt)
                            herramientas_usadas.append(tool_name)
                        except Exception as err:
                            messages.append(
                                ToolMessage(
                                    content=f"Error en herramienta {tool_name}: {str(err)}",
                                    tool_call_id=tool_call.get("id", f"call_{iteration}"),
                                )
                            )
                    
                    # Otras herramientas
                    elif tool_name in self.tools_map:
                        try:
                            tool_result = self.tools_map[tool_name].invoke(tool_args)
                            messages.append(
                                ToolMessage(
                                    content=str(tool_result),
                                    tool_call_id=tool_call.get("id", f"call_{iteration}"),
                                )
                            )
                            last_tool_result = tool_result
                            herramientas_usadas.append(tool_name)
                        except Exception as err:
                            error_msg = f"Error al ejecutar la herramienta {tool_name}: {str(err)}"
                            print(f"❌ {error_msg}")
                            messages.append(
                                ToolMessage(
                                    content=error_msg,
                                    tool_call_id=tool_call.get("id", f"call_{iteration}"),
                                )
                            )
                    else:
                        # Herramienta no registrada
                        messages.append(
                            ToolMessage(
                                content=f"⚠️ Herramienta '{tool_name}' no está disponible.",
                                tool_call_id=tool_call.get("id", f"call_{iteration}"),
                            )
                        )
                
                iteration += 1
            else:
                # Respuesta final del modelo
                response_text = response.content if hasattr(response, 'content') else str(response)
                break
        
        # --- 8. MANEJO DE CASOS LÍMITE ---
        if not response_text or response_text.strip() == "":
            if last_tool_result:
                response_text = f"Basado en la información obtenida: {str(last_tool_result)[:500]}"
            else:
                response_text = "Lo siento, no pude generar una respuesta. ¿Podrías reformular tu consulta?"
        
        if iteration >= self.max_iterations:
            response_text += "\n\n⚠️ Se alcanzó el límite de iteraciones. Si necesitas más información, por favor sé más específico."

        # --- 9. PERSISTIR CONVERSACIÓN ---
        if history:
            try:
                history.add_user_message(user_text)
                history.add_ai_message(response_text)
            except Exception as e:
                print(f"⚠️ Error guardando en Redis: {e}")

        # --- 10. REGISTRAR PARA APRENDIZAJE ---
        if user_id and len(response_text) > 10:
            try:
                self._registrar_interaccion(
                    user_id=user_id,
                    canal_id=canal_id,
                    user_text=user_text,
                    response_text=response_text,
                    herramientas_usadas=herramientas_usadas
                )
            except Exception as e:
                print(f"⚠️ Error registrando interacción: {e}")

        return response_text

    # ============================================================
    # 6. MANEJADOR DE SALUDOS
    # ============================================================
    
    def _handle_greeting(self, user_id: Optional[str] = None) -> str:
        """
        Maneja saludos simples con contexto personalizado.
        """
        if not user_id:
            return "👋 ¡Hola! Soy tu asistente de SolidSET. ¿En qué puedo ayudarte hoy?"
        
        try:
            contexto_obj = self.sistema_aprendizaje.obtener_contexto_usuario(user_id)
            if contexto_obj:
                nombre = contexto_obj.usuario.nombre or "operario"
                rol = contexto_obj.usuario.rol or "técnico"
                canales = len(contexto_obj.canales_acceso)
                
                return f"""👋 ¡Hola {nombre}! Soy tu asistente de mecanizado.

📋 Veo que eres **{rol}** y tienes acceso a **{canales} canales** de trabajo.

¿En qué área necesitas asistencia hoy?
- 🔧 Diagnóstico de máquinas
- 📊 Consulta de datos de producción
- 📚 Documentación técnica
- ⚙️ Recomendaciones de mantenimiento

¡Dime qué necesitas!"""
            else:
                return "👋 ¡Hola! Soy tu asistente de SolidSET. ¿En qué puedo ayudarte hoy?"
        except Exception as e:
            print(f"⚠️ Error en saludo personalizado: {e}")
            return "👋 ¡Hola! Soy tu asistente de SolidSET. ¿En qué puedo ayudarte hoy?"

    # ============================================================
    # 7. MÉTODO DE UTILIDAD PARA DEPURACIÓN
    # ============================================================
    
    def clear_user_cache(self):
        """Limpia la caché de contextos de usuario."""
        self.user_context_cache = {}
        print("🧹 Caché de usuarios limpiada")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas de la caché."""
        return {
            "cache_size": len(self.user_context_cache),
            "cached_users": list(self.user_context_cache.keys()),
            "ttl_seconds": self.cache_ttl
        }


# ============================================================
# 8. FUNCIÓN DE FÁBRICA PARA CREAR INSTANCIAS
# ============================================================

def create_agent() -> MachiningAgent:
    """Crea una instancia del agente con configuración por defecto."""
    return MachiningAgent()


# ============================================================
# 9. PRUEBA RÁPIDA (para desarrollo)
# ============================================================

if __name__ == "__main__":
    # Prueba básica del agente
    print("🧪 Probando agente...")
    
    agent = MachiningAgent()
    
    # Probar con un usuario de ejemplo
    test_response = agent.analyze_event_with_dialogue(
        session_id="test_session_001",
        user_text="Hola, necesito revisar el estado de la máquina",
        user_id="USR001"
    )
    
    print("\n" + "="*60)
    print("RESPUESTA DE PRUEBA:")
    print("="*60)
    print(test_response)
    print("="*60)
    print(f"\n📊 Estadísticas de caché: {agent.get_cache_stats()}")