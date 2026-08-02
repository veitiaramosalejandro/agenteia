import hashlib
import re
from urllib import error as urlerror
from urllib.request import urlopen
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

    def _is_llm_connection_error(self, exc: Exception) -> bool:
        """Detecta fallos típicos de conexión al endpoint del LLM/Ollama."""
        text = str(exc).lower()
        connection_hints = [
            "10061",
            "connection refused",
            "actively refused",
            "failed to establish a new connection",
            "max retries exceeded",
            "nodename nor servname provided",
            "no connection could be made",
            "nenhuma ligação pôde ser feita",
        ]
        return any(hint in text for hint in connection_hints)

    def _probe_ollama_tags(self, timeout_seconds: float = 2.0) -> str:
        """Realiza una comprobación corta al endpoint /api/tags para diagnóstico."""
        base = (settings.OLLAMA_BASE_URL or "").rstrip("/")
        if not base:
            return "OLLAMA_BASE_URL vacía"

        target = f"{base}/api/tags"
        try:
            with urlopen(target, timeout=timeout_seconds) as response:
                status_code = int(getattr(response, "status", 200))
                return f"HTTP {status_code} en {target}"
        except urlerror.HTTPError as e:
            return f"HTTP {int(getattr(e, 'code', 0))} en {target}"
        except Exception as e:
            return f"sin respuesta en {target}: {e}"

    def _build_llm_connection_error_message(self) -> str:
        """Genera mensaje de error claro cuando el LLM no está alcanzable."""
        probe = self._probe_ollama_tags()
        return (
            "⚠️ No pude conectar con el modelo LLM en este momento. "
            f"URL configurada: {settings.OLLAMA_BASE_URL}. "
            f"Diagnóstico rápido: {probe}. "
            "Verifica que Ollama esté encendido y que la URL/puerto sean correctos."
        )

    def _is_last_chat_message_intent(self, user_text: str) -> bool:
        """Detecta solicitudes para obtener el último mensaje del chat desde BD."""
        text = (user_text or "").strip().lower()
        if not text:
            return False
        has_last = any(
            k in text
            for k in [
                "ultimo mensaje",
                "último mensaje",
                "ultimos mensajes",
                "últimos mensajes",
                "last message",
                "last messages",
            ]
        ) or ("ultimo" in text or "último" in text or "ultimos" in text or "últimos" in text)
        has_chat_scope = any(k in text for k in ["chat", "canal", "contexto de la base de datos", "base de datos"])
        return has_last and has_chat_scope

    def _extract_last_messages_limit(self, user_text: str) -> int:
        """Extrae la cantidad solicitada de últimos mensajes; por defecto 1 y máximo 20."""
        text = (user_text or "").lower()
        match = re.search(r"\b(\d{1,2})\b", text)
        if not match:
            return 1
        requested = int(match.group(1))
        return max(1, min(requested, 20))

    def _extract_last_messages_offset(self, user_text: str) -> int:
        """Detecta desplazamientos como 'anterior al último' o 'penúltimo'."""
        text = (user_text or "").lower()
        previous_patterns = [
            r"anterior\s+al\s+ultim[oa]",
            r"antes\s+del\s+ultim[oa]",
            r"penultim[oa]",
        ]
        for pattern in previous_patterns:
            if re.search(pattern, text):
                return 1
        return 0

    def _extract_target_person_name(self, user_text: str) -> Optional[str]:
        """Extrae nombre de persona en consultas tipo 'mensaje que haya escrito X en el canal'."""
        text = (user_text or "").strip()
        if not text:
            return None

        patterns = [
            r"escrit[oa]\s+(.+?)\s+en\s+el\s+canal",
            r"de\s+(.+?)\s+en\s+el\s+canal",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                candidate = (match.group(1) or "").strip(" .,:;!?\"'")
                if candidate and len(candidate) >= 3:
                    return candidate
        return None

    def _is_channel_members_intent(self, user_text: str) -> bool:
        """Detecta solicitudes de listar usuarios/recurso pertenecientes al canal."""
        text = (user_text or "").strip().lower()
        if not text:
            return False

        has_canal = any(k in text for k in ["canal", "workroom", "sala", "channel"])
        has_membership = any(
            k in text
            for k in [
                "usuarios",
                "usuario",
                "miembros",
                "recursos",
                "recurso",
                "pertenecen",
                "pertenecen",
                "pertenece",
            ]
        )
        return has_canal and has_membership

    def _resolve_channel_members_from_db(self, user_id: str, canal_id: Optional[str]) -> Optional[str]:
        """Resuelve miembros/recurso del canal directamente desde BD."""
        effective_canal = (canal_id or "").strip()
        if not effective_canal:
            return (
                "⚠️ Para listar usuarios recurso necesito el ID del canal. "
                "Envía canal_id o usa session_id con el ID del canal."
            )

        rows = self.sistema_aprendizaje.obtener_usuarios_recurso_del_canal(
            user_id=user_id,
            canal_id=effective_canal,
            limit=80,
        )

        if not rows:
            return (
                "⚠️ No pude obtener usuarios recurso de este canal desde la base de datos "
                "(sin acceso, sin datos o sin conectividad SQL)."
            )

        channel_name = rows[0].get("channel_name") or "Canal sin nombre"
        formatted = []
        for idx, row in enumerate(rows, start=1):
            display_name = row.get("display_name") or "Sin nombre"
            resource_id = row.get("resource_id") or "sin_resource_id"
            username = row.get("username")
            username_text = f" | user: {username}" if username else ""
            formatted.append(f"{idx}. {display_name} ({resource_id}){username_text}")

        return (
            f"Usuarios recurso del canal '{channel_name}' (base de datos):\n"
            + "\n".join(formatted)
        )

    def _resolve_last_chat_message_from_db(self, user_id: str, canal_id: Optional[str], user_text: str) -> Optional[str]:
        """Resuelve de forma directa mensajes recientes del canal desde la BD del sistema."""
        try:
            requested_limit = self._extract_last_messages_limit(user_text)
            requested_offset = self._extract_last_messages_offset(user_text)
            missing_canal_scope = not bool((canal_id or "").strip())
            target_user_id = user_id
            target_person = self._extract_target_person_name(user_text)
            if target_person:
                resolved_target_user = self.sistema_aprendizaje.obtener_recurso_id_por_nombre(target_person)
                if resolved_target_user:
                    target_user_id = resolved_target_user

            rows = self.sistema_aprendizaje.obtener_mensajes_chat_desde_bd(
                user_id=user_id,
                canal_id=canal_id,
                limit=max(20, requested_limit + requested_offset),
                offset=0,
                sender_resource_id=target_user_id if target_person else None,
            )
            if not rows:
                return None

            selected = rows[requested_offset:requested_offset + requested_limit]
            if not selected:
                return "No encontré suficientes mensajes en el canal para esa posición solicitada."

            formatted = []
            for row in selected:
                ts = row.get("timestamp")
                ts_text = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else "sin_fecha"
                channel_name = row.get("channel_name") or "Canal sin nombre"
                sender_name = row.get("sender_display_name")
                sender_text = f" [{sender_name}]" if sender_name else ""
                msg_text = row.get("message") or ""
                formatted.append(f"[{ts_text}] ({channel_name}){sender_text} {msg_text}")

            scope_note = ""
            if missing_canal_scope:
                scope_note = "[Aviso: no se recibió canal_id; se usó el canal más reciente accesible para este usuario.] "

            if requested_limit == 1 and requested_offset == 0:
                if target_person:
                    return f"{scope_note}Último mensaje de {target_person} en el canal (base de datos): {formatted[0]}"
                return f"{scope_note}Último mensaje del canal en base de datos: {formatted[0]}"

            if requested_limit == 1 and requested_offset == 1:
                if target_person:
                    return f"{scope_note}Mensaje anterior al último de {target_person} en el canal (base de datos): {formatted[0]}"
                return f"{scope_note}Mensaje anterior al último del canal en base de datos: {formatted[0]}"

            joined = "\n".join(f"{idx}. {line}" for idx, line in enumerate(formatted, start=1))
            if requested_offset > 0:
                return (
                    f"{scope_note}Mensajes del canal desde la posición {requested_offset + 1} en base de datos:\n"
                    f"{joined}"
                )

            if target_person:
                return (
                    f"{scope_note}Últimos {len(selected)} mensajes de {target_person} en el canal (base de datos):\n"
                    f"{joined}"
                )

            return (
                f"{scope_note}Últimos {len(selected)} mensajes del canal en base de datos:\n"
                f"{joined}"
            )
        except Exception:
            return None

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
                    unique_results = []
                    seen_results = set()
                    for item in resultados:
                        normalized = item.strip()
                        if normalized not in seen_results:
                            seen_results.add(normalized)
                            unique_results.append(item)
                        if len(unique_results) >= 3:
                            break
                    return "\n\n".join(unique_results[:3])
             
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
            if self._is_llm_connection_error(e):
                print(
                    "⚠️ Error generando resumen: LLM no alcanzable "
                    f"(OLLAMA_BASE_URL={settings.OLLAMA_BASE_URL}) | {e}"
                )
            else:
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
                               herramientas_usadas: List[str],
                               session_id: str):
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
            
            # Resolver canales objetivo: canal explícito o canales del usuario desde BD.
            canales_objetivo = []
            if canal_id:
                canales_objetivo = [canal_id]
            else:
                try:
                    contexto = self.sistema_aprendizaje.obtener_contexto_usuario(user_id)
                    if contexto and contexto.canales_acceso:
                        canales_objetivo = [c.id for c in contexto.canales_acceso if getattr(c, "id", None)]
                except Exception:
                    canales_objetivo = []

            if not canales_objetivo:
                canales_objetivo = ["canal_general"]

            # Registrar actividad en cada canal asociado, para aprendizaje contextual por conversación.
            from app.system.schema import Actividad
            timestamp_now = datetime.now()
            for idx, canal_target in enumerate(canales_objetivo):
                actividad = Actividad(
                    id=f"interaccion_{session_id}_{timestamp_now.timestamp()}_{idx}",
                    recurso_humano_id=user_id,
                    canal_id=canal_target,
                    tipo=tipo_actividad,
                    descripcion=descripcion,
                    timestamp=timestamp_now,
                    metadatos={
                        "session_id": session_id,
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
            user_id: Username del usuario (para contexto personalizado)
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

        previous_user_text = None
        if history:
            try:
                for msg in reversed(list(history.messages)):
                    if isinstance(msg, HumanMessage):
                        previous_user_text = msg.content
                        break
            except Exception as e:
                print(f"⚠️ Error leyendo historial previo: {e}")

        # --- 3. DETECTAR SALUDOS ---
        clean_text = user_text.strip().lower()
        saludos = ["hola", "hola!", "buenos dias", "buenas tardes", "ola", "hello", "hi", "hey", "buenas"]
        
        if clean_text in saludos:
            response_text = self._handle_greeting(user_id)

            # Persistir también saludos para mantener trazabilidad conversacional.
            if history:
                try:
                    history.add_user_message(user_text)
                    history.add_ai_message(response_text)
                except Exception as e:
                    print(f"⚠️ Error guardando saludo en Redis: {e}")

            # Aprender saludos por canal/sesión para contexto histórico.
            if user_id and len(response_text) > 10:
                try:
                    self._registrar_interaccion(
                        user_id=user_id,
                        canal_id=canal_id,
                        user_text=user_text,
                        response_text=response_text,
                        herramientas_usadas=[],
                        session_id=session_id,
                    )
                except Exception as e:
                    print(f"⚠️ Error registrando saludo para aprendizaje: {e}")

            return response_text

        # --- 3.1 CONSULTA DIRECTA DE ÚLTIMO MENSAJE EN CHAT (BD) ---
        if user_id and self._is_last_chat_message_intent(user_text):
            direct_response = self._resolve_last_chat_message_from_db(user_id, canal_id, user_text)
            if direct_response is not None:
                if history:
                    try:
                        history.add_user_message(user_text)
                        history.add_ai_message(direct_response)
                    except Exception as e:
                        print(f"⚠️ Error guardando respuesta directa en Redis: {e}")

                try:
                    self._registrar_interaccion(
                        user_id=user_id,
                        canal_id=canal_id,
                        user_text=user_text,
                        response_text=direct_response,
                        herramientas_usadas=[],
                        session_id=session_id,
                    )
                except Exception as e:
                    print(f"⚠️ Error registrando interacción directa para aprendizaje: {e}")

                return direct_response

            # Evita caer al LLM cuando la intención era estrictamente recuperar chat desde BD.
            return (
                "⚠️ No pude consultar el historial del canal en la base de datos en este momento. "
                "Verifica la conectividad de SQL Server e inténtalo nuevamente."
            )

        # --- 4. OBTENER CONTEXTOS ---
        
        # 4.1 Contexto del usuario (canales, rol, permisos)
        contexto_usuario = ""
        if user_id:
            contexto_usuario = self._get_user_context(user_id)
        
        # 4.2 Contexto RAG (documentos técnicos)
        rag_context = self.sistema_aprendizaje.consultar_documentacion(user_text)

        # 4.3 Contexto conversacional desde BD (chat + canal)
        chat_context_bd = ""
        if user_id:
            chat_context_bd = self.sistema_aprendizaje.obtener_contexto_chat_desde_bd(
                user_id=user_id,
                canal_id=canal_id,
                limit=8,
            )
        
        # 4.4 Aprendizaje relevante (actividades pasadas similares)
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
        
        messages = [system_msg]

        if chat_context_bd:
            chat_msg = SystemMessage(
                content=f"🗂️ CONTEXTO RECIENTE DESDE BASE DE DATOS (CHAT/CANAL):\n{chat_context_bd}"
            )
            messages.append(chat_msg)
        
        # Añadir aprendizaje relevante si existe
        if aprendizaje_relevante and "No hay conocimiento" not in aprendizaje_relevante:
            aprendizaje_msg = SystemMessage(
                content=f"🧠 CONOCIMIENTO APRENDIDO DE ACTIVIDADES PREVIAS:\n{aprendizaje_relevante}"
            )
            messages.append(aprendizaje_msg)

        # Mensaje de contexto RAG
        rag_msg = HumanMessage(
            content=f"📚 DOCUMENTACIÓN TÉCNICA RELEVANTE:\n{rag_context if rag_context else 'No hay documentación específica para esta consulta.'}"
        )
        messages.append(rag_msg)

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
                if self._is_llm_connection_error(e):
                    return self._build_llm_connection_error_message()
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
                    herramientas_usadas=herramientas_usadas,
                    session_id=session_id,
                )
            except Exception as e:
                print(f"⚠️ Error registrando interacción: {e}")

            try:
                reaction = self.sistema_aprendizaje.analyze_reaction_patterns(
                    user_text=user_text,
                    agent_response=response_text,
                    previous_user_text=previous_user_text,
                )
                if reaction.get("signal") != "sin_senal":
                    self.sistema_aprendizaje.registrar_feedback_usuario(
                        user_id=user_id,
                        canal_id=canal_id,
                        session_id=session_id,
                        user_text=user_text,
                        agent_response=response_text,
                        feedback_type="implicit",
                        reason=reaction.get("signal"),
                        previous_user_text=previous_user_text,
                        implicit=True,
                    )
            except Exception as e:
                print(f"⚠️ Error registrando feedback implícito: {e}")

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