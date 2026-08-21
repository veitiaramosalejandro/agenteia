"""Orquestación LangGraph para las rutas de conversación del agente SolidSET."""

import re
from time import perf_counter
from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langchain_core.messages import HumanMessage, SystemMessage


class AgentGraphState(TypedDict, total=False):
    session_id: str
    user_text: str
    user_id: Optional[str]
    canal_id: Optional[str]
    meeting_id: Optional[str]
    meeting_code: Optional[str]
    message_kind: Optional[str]
    message_category: Optional[str]
    message_metadata: Optional[dict[str, Any]]
    auto_reply_mode: bool
    tool_allowlist: Optional[set[str]]
    route: str
    response: str
    error: str
    started_at: float
    elapsed_seconds: float


class SolidSETOrchestrator:
    """Clasifica, ejecuta y valida solicitudes mediante un StateGraph compilado."""

    def __init__(self, agent: Any):
        self.agent = agent
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(AgentGraphState)
        workflow.add_node("classify", self._classify)
        workflow.add_node("general_conversation", self._execute_general)
        workflow.add_node("external_web", self._execute_external)
        workflow.add_node("work_sql_rag", self._execute_work)
        workflow.add_node("validate", self._validate)
        workflow.add_edge(START, "classify")
        workflow.add_conditional_edges(
            "classify",
            self._route_after_classification,
            {
                "general_conversation": "general_conversation",
                "external_web": "external_web",
                "work_sql_rag": "work_sql_rag",
            },
        )
        workflow.add_edge("general_conversation", "validate")
        workflow.add_edge("external_web", "validate")
        workflow.add_edge("work_sql_rag", "validate")
        workflow.add_edge("validate", END)
        return workflow.compile(name="solidset-agent-orchestrator")

    def _classify(self, state: AgentGraphState) -> AgentGraphState:
        user_text = state.get("user_text", "")
        lowered = user_text.lower()
        metadata = dict(state.get("message_metadata") or {})
        coding_terms = (
            "código", "codigo", "programa", "python", "javascript", "c#", ".net",
            "sql", "consulta", "query", "base de datos", "api", "endpoint",
            "docker", "error", "stack trace", "función", "metodo", "método",
        )
        reasoning_terms = (
            "analiza", "razona", "compara", "estrategia", "planifica", "plan de",
            "causa raíz", "causa raiz", "por qué", "porque ocurre", "evalúa", "evalua",
        )
        is_general = getattr(self.agent, "_is_general_conversation", lambda _text: False)
        if metadata.get("response_suggestion_mode"):
            # A suggestion must be grounded in the requester's own agent
            # knowledge. It must not escape to the public web merely because
            # the quoted text is outside the internal-domain classifier.
            route = "work_sql_rag"
        elif is_general(user_text):
            route = "general_conversation"
        elif (
            self.agent._is_external_information_query(user_text)
            or not self.agent._is_internal_domain_query(user_text)
        ):
            route = "external_web"
        else:
            route = "work_sql_rag"
        if any(term in lowered for term in coding_terms):
            capability = "coding"
        elif any(term in lowered for term in reasoning_terms):
            capability = "reasoning"
        elif route == "external_web":
            capability = "external_web"
        else:
            capability = "general"
        metadata["model_capability"] = capability
        print(
            f"🧭 LangGraph route={route} capability={capability} "
            f"session={state.get('session_id', '')}"
        )
        return {
            "route": route,
            "message_metadata": metadata,
            "started_at": state.get("started_at") or perf_counter(),
        }

    @staticmethod
    def _route_after_classification(state: AgentGraphState) -> str:
        return state.get("route", "work_sql_rag")

    def _execute_external(self, state: AgentGraphState) -> AgentGraphState:
        response = self.agent.analyze_event_with_dialogue(
            session_id=state.get("session_id", ""),
            user_text=state.get("user_text", ""),
            user_id=state.get("user_id"),
            canal_id=state.get("canal_id"),
            meeting_id=state.get("meeting_id"),
            meeting_code=state.get("meeting_code"),
            message_kind=state.get("message_kind"),
            message_category=state.get("message_category"),
            message_metadata=state.get("message_metadata"),
            tool_allowlist={"google_web_search"},
            auto_reply_mode=bool(state.get("auto_reply_mode")),
            external_query_mode=True,
        )
        return {"response": response}

    def _execute_general(self, state: AgentGraphState) -> AgentGraphState:
        response = self.agent.analyze_event_with_dialogue(
            session_id=state.get("session_id", ""),
            user_text=state.get("user_text", ""),
            user_id=state.get("user_id"),
            canal_id=state.get("canal_id"),
            meeting_id=state.get("meeting_id"),
            meeting_code=state.get("meeting_code"),
            message_kind=state.get("message_kind"),
            message_category=state.get("message_category"),
            message_metadata=state.get("message_metadata"),
            tool_allowlist=set(),
            auto_reply_mode=bool(state.get("auto_reply_mode")),
            general_conversation_mode=True,
        )
        return {"response": response}

    def _execute_work(self, state: AgentGraphState) -> AgentGraphState:
        response = self.agent.analyze_event_with_dialogue(
            session_id=state.get("session_id", ""),
            user_text=state.get("user_text", ""),
            user_id=state.get("user_id"),
            canal_id=state.get("canal_id"),
            meeting_id=state.get("meeting_id"),
            meeting_code=state.get("meeting_code"),
            message_kind=state.get("message_kind"),
            message_category=state.get("message_category"),
            message_metadata=state.get("message_metadata"),
            tool_allowlist=state.get("tool_allowlist"),
            auto_reply_mode=bool(state.get("auto_reply_mode")),
            external_query_mode=False,
        )
        return {"response": response}

    def _validate(self, state: AgentGraphState) -> AgentGraphState:
        response = str(state.get("response") or "").strip()
        if not response:
            response = "No pude generar una respuesta en este momento. Inténtalo nuevamente."
        elif self.agent._looks_like_raw_tool_response(response):
            response = (
                "No pude presentar de forma segura los datos obtenidos. "
                "Inténtalo nuevamente en unos instantes."
            )
        response = self._ensure_response_language(
            state.get("user_text", ""),
            response,
            state.get("message_metadata"),
        )
        response = self._hide_internal_implementation_details(
            response,
            self.agent._detect_user_language(state.get("user_text", "")),
        )
        started_at = state.get("started_at") or perf_counter()
        elapsed = perf_counter() - started_at
        print(f"✅ LangGraph completed route={state.get('route')} elapsed={elapsed:.2f}s")
        return {"response": response, "elapsed_seconds": elapsed}

    @staticmethod
    def _hide_internal_implementation_details(response: str, language: str = "es") -> str:
        """Prevent implementation details from leaking into user-facing answers."""
        replacement = {
            "pt": "a informação disponível",
            "en": "the available information",
            "es": "la información disponible",
        }.get(language, "la información disponible")
        internal_source = re.compile(
            r"(?i)(?:"
            r"(?:informaci[oó]n\s+(?:reciente\s+)?recuperada\s+desde\s+)|"
            r"(?:informa[cç][aã]o\s+(?:recente\s+)?(?:obtida|recuperada)\s+(?:da|desde\s+a)\s+)|"
            r"(?:information\s+(?:recently\s+)?retrieved\s+from\s+)"
            r")?"
            r"(?:mi\s+|my\s+|the\s+|la\s+|el\s+|a\s+minha\s+|o\s+meu\s+)?"
            r"(?:vectorial\s+knowledge\s+base|vector\s+knowledge\s+base|"
            r"base\s+(?:de\s+)?conocimiento\s+vectorial|base\s+vectorial|"
            r"base\s+de\s+conhecimento\s+vetorial|qdrant|rag|embeddings?)"
        )
        sanitized = internal_source.sub(replacement, str(response or ""))
        sanitized = re.sub(
            r"(?i)\b(?:la\s+la\s+informaci[oó]n|the\s+the\s+available|"
            r"a\s+a\s+informa[cç][aã]o)",
            lambda _match: {
                "pt": "a informação",
                "en": "the available",
                "es": "la información",
            }.get(language, "la información"),
            sanitized,
        )
        sanitized = re.sub(r"\s+([,.;:!?])", r"\1", sanitized)
        sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
        return sanitized.strip()

    def _ensure_response_language(
        self,
        user_text: str,
        response: str,
        message_metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Garantiza ES/PT/EN también para respuestas deterministas construidas por código."""
        expected = self.agent._detect_user_language(user_text)
        detected = self.agent._detect_user_language(response)
        if expected == detected or len(response) < 8:
            return response
        target = {"es": "español", "pt": "português", "en": "English"}[expected]
        try:
            selected_llm, _, _ = self.agent.get_llm_for_metadata(message_metadata)
            translated = selected_llm.invoke([
                SystemMessage(content=(
                    f"Translate the response to {target}. Preserve names, figures, dates, Markdown and "
                    "technical identifiers exactly. Return only the translated response."
                )),
                HumanMessage(content=response),
            ])
            text = translated.content if hasattr(translated, "content") else str(translated)
            return str(text or "").strip() or response
        except Exception as exc:
            print(f"⚠️ LangGraph language normalization failed: {exc}")
            return response

    def invoke(
        self,
        *,
        session_id: str,
        user_text: str,
        user_id: Optional[str] = None,
        canal_id: Optional[str] = None,
        meeting_id: Optional[str] = None,
        meeting_code: Optional[str] = None,
        message_kind: Optional[str] = None,
        message_category: Optional[str] = None,
        message_metadata: Optional[dict[str, Any]] = None,
        tool_allowlist: Optional[set[str]] = None,
        auto_reply_mode: bool = False,
    ) -> str:
        result = self.graph.invoke({
            "session_id": session_id,
            "user_text": user_text,
            "user_id": user_id,
            "canal_id": canal_id,
            "meeting_id": meeting_id,
            "meeting_code": meeting_code,
            "message_kind": message_kind,
            "message_category": message_category,
            "message_metadata": message_metadata,
            "tool_allowlist": tool_allowlist,
            "auto_reply_mode": auto_reply_mode,
            "started_at": perf_counter(),
        })
        response = str(result.get("response") or "").strip()
        identity_service = getattr(self.agent, "identity_service", None)
        try:
            if identity_service is not None:
                identity_service.remember_turn(
                    session_id=session_id,
                    user_id=user_id,
                    user_text=user_text,
                    agent_response=response,
                )
        except Exception as exc:
            # La identidad enriquece el diálogo, pero nunca debe impedir responder.
            print(f"⚠️ No se pudo actualizar la memoria de identidad: {exc}")
        return response
