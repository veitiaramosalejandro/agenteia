from __future__ import annotations

from app.redis_runtime import redis_client

import ast
import asyncio
import hashlib
import json
import re
import threading
import uuid
from collections import OrderedDict
from datetime import datetime
from time import time
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
import pymssql
import redis

from app.agent.authorization import normalize_bool, normalize_uuid, resolve_resource_table
from app.agent.tools import solidset_send_chat_message
from app.config import settings
from app.interactive_priority import async_interactive_work
from app.knowledge_provenance import USER_ASSERTION_SOURCE
from app.connectors.db_client import (
    agent_learning_enabled,
    ensure_payload_agent_workroom_assignments,
    get_active_agents_for_workroom,
    get_agent_knowledge,
    get_solidset_instance,
    save_agent_knowledge,
    touch_agent_session,
)
from app.connectors.solidset_data_api import SolidSETDataAPIError
from app.connectors.solidset_sql import (
    instance_context as solidset_sql_instance_context,
)
from app.response_queue import AgentResponseQueue
from app.services.response_status import update as _update_response_status
from app.system.reaction_capture import get_agent_reinforcement_context
from app.system.resource_ingest import verify_and_sync_solidset_agent_mapping
from app.system.schema import Actividad

agent = None
orchestrator = None
response_queue: AgentResponseQueue | None = None
_dialogue_redis = redis_client(settings.REDIS_URL, decode_responses=True)
_auto_reply_lock = threading.Lock()
_auto_reply_seen_fingerprints: "OrderedDict[str, float]" = OrderedDict()
_auto_reply_max_seen = 2000
_auto_reply_background_tasks: set[asyncio.Task] = set()
_auto_reply_followups: dict[str, float] = {}


def configure(
    runtime_agent, runtime_orchestrator, runtime_response_queue: AgentResponseQueue
) -> None:
    global agent, orchestrator, response_queue
    agent = runtime_agent
    orchestrator = runtime_orchestrator
    response_queue = runtime_response_queue


def _auto_reply_seen(fingerprint: str) -> bool:
    key = (fingerprint or "").strip()
    if not key:
        return False
    with _auto_reply_lock:
        return key in _auto_reply_seen_fingerprints


def _remember_auto_reply_fingerprint(fingerprint: str) -> None:
    key = (fingerprint or "").strip()
    if not key:
        return
    with _auto_reply_lock:
        _auto_reply_seen_fingerprints[key] = time()
        _auto_reply_seen_fingerprints.move_to_end(key)
        while len(_auto_reply_seen_fingerprints) > _auto_reply_max_seen:
            _auto_reply_seen_fingerprints.popitem(last=False)


def _auto_reply_followup_key(candidate: dict) -> str:
    scope = (
        str(candidate.get("reply_resource") or candidate.get("channel_id") or "")
        .strip()
        .lower()
    )
    sender = (
        str(candidate.get("sender_resource") or candidate.get("sender_name") or "")
        .strip()
        .lower()
    )
    digest = hashlib.sha256(f"{scope}|{sender}".encode("utf-8")).hexdigest()
    return f"machining:auto-reply:followup:{digest}"


def _has_active_auto_reply_followup(candidate: dict) -> bool:
    key = _auto_reply_followup_key(candidate)
    try:
        return bool(_dialogue_redis.exists(key))
    except redis.RedisError:
        with _auto_reply_lock:
            expires_at = _auto_reply_followups.get(key, 0.0)
            if expires_at <= time():
                _auto_reply_followups.pop(key, None)
                return False
            return True


def _remember_auto_reply_followup(candidate: dict) -> None:
    key = _auto_reply_followup_key(candidate)
    ttl = max(30, settings.SOLIDSET_AUTO_REPLY_FOLLOWUP_TTL_SECONDS)
    try:
        _dialogue_redis.setex(key, ttl, "1")
    except redis.RedisError:
        with _auto_reply_lock:
            _auto_reply_followups[key] = time() + ttl


def _is_self_sender(sender_resource: str, sender_name: str) -> bool:
    own_resource = (settings.SOLIDSET_RESOURCE_ID or "").strip().lower()
    sender_resource_norm = (sender_resource or "").strip().lower()
    # Sender.resource es la identidad canónica. El nombre/login puede coincidir
    # con alias visibles o venir incompleto y no debe descartar usuarios reales.
    return bool(
        own_resource and sender_resource_norm and own_resource == sender_resource_norm
    )


def _sanitize_auto_reply_input(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        return ""
    mention_tokens = {
        (settings.SOLIDSET_AUTO_REPLY_MENTION_TOKEN or "").strip(),
        "@agente",
        "@asistente",
        "@agent",
        "@assistant",
    }
    for mention_token in (token for token in mention_tokens if token):
        text = re.sub(re.escape(mention_token), " ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"^\s*(?:agente|asistente|agent|assistant)\s*[,;:\-]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = " ".join(text.split())
    max_len = max(80, settings.SOLIDSET_AUTO_REPLY_MAX_INPUT_CHARS)
    return text[:max_len].strip()


def _looks_like_question_or_request(raw_text: str) -> bool:
    text = " ".join((raw_text or "").strip().lower().split())
    if not text:
        return False
    if "?" in text or "¿" in text:
        return True
    starters = (
        "qué ",
        "que ",
        "cómo ",
        "como ",
        "cuál ",
        "cual ",
        "cuándo ",
        "cuando ",
        "dónde ",
        "donde ",
        "quién ",
        "quien ",
        "por qué ",
        "puedes ",
        "podrías ",
        "dime ",
        "busca ",
        "consulta ",
        "explica ",
        "ayúdame ",
        "ayudame ",
        "necesito ",
        "quiero que ",
        "haz ",
        "genera ",
        "resume ",
        "analiza ",
        "compara ",
        "muéstrame ",
        "muestrame ",
        "indícame ",
        "indicame ",
        "what ",
        "how ",
        "when ",
        "where ",
        "who ",
        "why ",
        "can you ",
        "please ",
        "i need ",
        "i want ",
        "give me ",
        "tell me ",
        "show me ",
        "generate ",
        "summarize ",
        "analyse ",
        "analyze ",
        "compare ",
        "o que ",
        "como ",
        "quando ",
        "onde ",
        "quem ",
        "por que ",
        "pode ",
        "procura ",
        "preciso ",
        "quero que ",
        "faça ",
        "faz ",
        "gera ",
        "resume ",
        "analisa ",
        "compara ",
        "mostra ",
        "indica ",
        "diz-me ",
    )
    return text.startswith(starters)


def _is_conversational_continuation(raw_text: str) -> bool:
    text = " ".join(str(raw_text or "").strip().lower().split()).rstrip(".!… ")
    return text in {
        "sí",
        "si",
        "sim",
        "yes",
        "no",
        "não",
        "nao",
        "continúa",
        "continua",
        "continue",
        "prossegue",
        "pode continuar",
        "puedes continuar",
        "de acuerdo",
        "está bien",
        "esta bien",
        "ok",
        "vale",
        "correcto",
        "correto",
        "right",
    }


def _is_informational_learning_message(raw_text: str) -> bool:
    """Detect factual/declarative input that should be learned, not answered."""
    text = " ".join(str(raw_text or "").strip().split())
    lowered = text.lower()
    if (
        not text
        or _looks_like_question_or_request(text)
        or _is_conversational_continuation(text)
    ):
        return False
    if any(
        term in lowered
        for term in (
            "gracias",
            "obrigado",
            "obrigada",
            "thank you",
            "thanks",
            "bom dia",
            "boa tarde",
            "boa noite",
            "buenos días",
            "buenas tardes",
            "buenas noches",
            "hello",
            "hola",
            "olá",
        )
    ):
        return False
    explicit_learning = (
        "aprende ",
        "recuerda que ",
        "ten en cuenta ",
        "para tu conocimiento ",
        "te informo que ",
        "fica a saber ",
        "tem em conta ",
        "para teu conhecimento ",
        "para seu conhecimento ",
        "informo que ",
        "learn this ",
        "remember that ",
        "for your information ",
        "keep in mind ",
    )
    factual_patterns = (
        r"\b(?:es|son|era|fue|tiene|tienen|representa|pertenece)\b",
        r"\b(?:é|são|era|foi|tem|têm|representa|pertence)\b",
        r"\b(?:is|are|was|were|has|have|represents|belongs)\b",
        r"\b(?:19|20)\d{2}\b",
    )
    return (
        len(text) >= 160
        or "\n" in str(raw_text or "")
        or any(lowered.startswith(prefix) for prefix in explicit_learning)
        or any(re.search(pattern, lowered) for pattern in factual_patterns)
    )


def _learning_acknowledgement(raw_text: str) -> str:
    if agent is not None:
        language = agent._detect_user_language(raw_text)
    else:
        normalized = " ".join(str(raw_text or "").lower().split())
        language = "pt" if re.search(
            r"\b(?:é|são|tem|têm|empresa|informação|representante|oficial)\b",
            normalized,
        ) else "en"
    messages = {
        "pt": "Agradeço a informação. Vou tê-la em conta.",
        "en": "Thank you for the information. I will take it into account.",
        "es": "Gracias por la información. La tendré en cuenta.",
    }
    return messages.get(language, messages["en"])


def _quoted_reply_is_learning_only(candidate: dict[str, Any]) -> bool:
    """Classify an informative reply to a quoted chat as learning-only."""
    if not str(candidate.get("quoted_message") or "").strip():
        return False
    text = " ".join(str(candidate.get("message") or "").strip().lower().split())
    if not text or _looks_like_question_or_request(text):
        return False
    if _is_conversational_continuation(text):
        return False
    return True


def _candidate_is_learning_only(candidate: dict[str, Any]) -> bool:
    return _quoted_reply_is_learning_only(
        candidate
    ) or _is_informational_learning_message(str(candidate.get("message") or ""))


def _message_mentions_agent(raw_text: str) -> bool:
    text = (raw_text or "").strip()
    if not text:
        return False
    configured = (settings.SOLIDSET_AUTO_REPLY_MENTION_TOKEN or "").strip()
    if configured and configured.lower() in text.lower():
        return True
    if re.search(
        r"(?<!\w)@(?:agente|asistente|agent|assistant)(?!\w)", text, flags=re.IGNORECASE
    ):
        return True
    return bool(
        re.match(
            r"^\s*(?:agente|asistente|agent|assistant)(?:\s*[,;:\-]\s*|\s+)(?=\S)",
            text,
            flags=re.IGNORECASE,
        )
    )


def _schedule_auto_replies(candidates: list[dict], request_id: str = "") -> None:
    """Mantiene una referencia fuerte y registra fallos de la tarea en background."""
    if not candidates:
        return
    print(
        f"🤖 Auto-respuesta encolada; candidatos={len(candidates)}",
        flush=True,
    )
    if request_id:
        for candidate in candidates:
            candidate["response_request_id"] = request_id
    task = asyncio.create_task(_process_auto_replies(candidates))
    _auto_reply_background_tasks.add(task)

    def _completed(done: asyncio.Task) -> None:
        _auto_reply_background_tasks.discard(done)
        try:
            sent = done.result()
            print(f"🤖 Procesamiento de auto-respuesta finalizado; enviadas={sent}")
        except asyncio.CancelledError:
            _update_response_status(request_id, "cancelled")
            print("⚠️ Procesamiento de auto-respuesta cancelado")
        except Exception as exc:
            _update_response_status(request_id, "failed", error=str(exc))
            print(f"❌ Error no controlado procesando auto-respuesta: {exc}")

    task.add_done_callback(_completed)


def _enqueue_auto_replies(
    payload: dict[str, Any],
    instance: dict[str, Any],
    request_id: str,
    chat_id: str,
) -> str:
    """Publica una solicitud durable; la API nunca ejecuta el LLM directamente."""
    stream_id = response_queue.enqueue(request_id, chat_id, payload, instance)
    print(
        f"📥 Auto-respuesta en Redis Stream request={request_id} stream_id={stream_id} "
        "payload=FrameworkMessage",
        flush=True,
    )
    return stream_id


def _is_safe_auto_reply_output(response_text: str) -> bool:
    """Impide publicar en SolidSET trazas, errores o resultados crudos de herramientas."""
    text = (response_text or "").strip().lower()
    if not text:
        return False
    forbidden = (
        "basado en la información obtenida: status=",
        "se alcanzó el límite de iteraciones",
        "validation error",
        "error al ejecutar la herramienta",
        "status=200",
        "method=get",
        "method=post",
        "body={",
        "body=[",
        "traceback (most recent call last)",
        "pydantic.dev",
        "resultado de la busqueda para responder el turno actual",
        "resultado de la búsqueda para responder el turno actual",
        "resultado da busca para responder o turno atual",
        '"tool": "query_sql_server"',
        "query_sql_server",
        "select * from",
    )
    return not any(marker in text for marker in forbidden)


def _weather_location_prompt(raw_text: str) -> Optional[str]:
    """Devuelve una aclaración si se pide el tiempo sin indicar ubicación."""
    text = " ".join((raw_text or "").strip().lower().split())
    weather_terms = (
        "tiempo",
        "tempo",
        "clima",
        "weather",
        "forecast",
        "meteorologia",
        "previsão",
        "previsao",
    )
    if not any(term in text for term in weather_terms):
        return None
    has_location = bool(
        re.search(
            r"\b(?:en|para|in|at|for|em)\s+[\wáéíóúüñãõç-]{2,}",
            text,
            flags=re.IGNORECASE,
        )
    )
    if has_location:
        return None
    if any(term in text for term in ("weather", "forecast", "today")):
        return "Which city or location would you like the weather forecast for?"
    if any(term in text for term in ("previsão", "previsao", "meteorologia", "hoje")):
        return "Para qual cidade ou localidade você quer consultar o tempo?"
    return "¿De qué ciudad o localidad quieres conocer el tiempo?"


def _local_arithmetic_response(raw_text: str) -> Optional[str]:
    """Resuelve expresiones aritméticas puras sin depender del LLM."""
    expression = " ".join((raw_text or "").strip().split()).strip(" ¿?¡!=")
    if not expression or len(expression) > 120:
        return None
    if not re.fullmatch(r"[0-9\s.,+\-*/%()]+", expression):
        return None
    expression = expression.replace(",", ".")
    binary_operations = {
        ast.Add: lambda left, right: left + right,
        ast.Sub: lambda left, right: left - right,
        ast.Mult: lambda left, right: left * right,
        ast.Div: lambda left, right: left / right,
        ast.FloorDiv: lambda left, right: left // right,
        ast.Mod: lambda left, right: left % right,
        ast.Pow: lambda left, right: left**right,
    }
    unary_operations = {
        ast.UAdd: lambda value: value,
        ast.USub: lambda value: -value,
    }

    def evaluate(node: ast.AST) -> int | float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary_operations:
            return unary_operations[type(node.op)](evaluate(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in binary_operations:
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow) and (
                abs(right) > 10 or abs(left) > 1_000_000
            ):
                raise ValueError("potencia fuera de límite")
            return binary_operations[type(node.op)](left, right)
        raise ValueError("expresión no permitida")

    try:
        result = evaluate(ast.parse(expression, mode="eval"))
        if not isinstance(result, (int, float)) or abs(result) > 1e15:
            return None
        rendered = str(int(result)) if float(result).is_integer() else f"{result:.10g}"
        return f"{expression} = **{rendered}**."
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
        return None


def _local_temporal_response(
    raw_text: str,
    *,
    time_zone: str,
    locale: str,
    country_code: str,
) -> Optional[str]:
    """Answers local date/time questions without allowing the LLM to invent a place."""
    text = " ".join((raw_text or "").strip().lower().split())
    # Tolera acentos omitidos, pluralizaciones y pequeñas variaciones habituales.
    # Estas preguntas nunca deben caer al LLM o a una búsqueda web: la fuente
    # autoritativa es el reloj de la instancia y su zona horaria configurada.
    asks_date = bool(
        re.search(
            r"\b(?:"
            r"qu[eé]\s+d[ií]as?\s+(?:es|e|[eé])\s+ho(?:y|je)|"
            r"(?:cu[aá]l\s+(?:es|e|[eé])\s+la\s+fecha|qual\s+(?:e|[eé])\s+a\s+data)|"
            r"(?:fecha\s+de\s+hoy|data\s+de\s+hoje)|"
            r"what\s+(?:day\s+is\s+today|is\s+today'?s\s+date)|today'?s\s+date"
            r")\b",
            text,
            flags=re.IGNORECASE,
        )
    )
    asks_time = bool(
        re.search(
            r"\b(?:"
            r"qu[eé]\s+horas?\s+(?:es|son|s[aã]o)|"
            r"hora\s+(?:actual|atual|local)|"
            r"what\s+time\s+is\s+it|current\s+time|local\s+time"
            r")\b",
            text,
            flags=re.IGNORECASE,
        )
    )
    if not asks_date and not asks_time:
        return None
    try:
        local_now = datetime.now(ZoneInfo(time_zone))
    except (ZoneInfoNotFoundError, ValueError):
        local_now = datetime.now(ZoneInfo("UTC"))
        time_zone = "UTC"

    # The incoming message always decides the response language. Locale only
    # selects regional conventions within that language (for example pt-PT).
    language = agent._detect_user_language(raw_text, locale)
    country_names = {
        "PT": {"pt": "Portugal", "es": "Portugal", "en": "Portugal"},
        "ES": {"pt": "Espanha", "es": "España", "en": "Spain"},
        "BR": {"pt": "Brasil", "es": "Brasil", "en": "Brazil"},
    }
    country = country_names.get(country_code.upper(), {}).get(
        language, country_code.upper()
    )
    weekdays = {
        "pt": (
            "segunda-feira",
            "terça-feira",
            "quarta-feira",
            "quinta-feira",
            "sexta-feira",
            "sábado",
            "domingo",
        ),
        "es": (
            "lunes",
            "martes",
            "miércoles",
            "jueves",
            "viernes",
            "sábado",
            "domingo",
        ),
        "en": (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ),
    }
    months = {
        "pt": (
            "janeiro",
            "fevereiro",
            "março",
            "abril",
            "maio",
            "junho",
            "julho",
            "agosto",
            "setembro",
            "outubro",
            "novembro",
            "dezembro",
        ),
        "es": (
            "enero",
            "febrero",
            "marzo",
            "abril",
            "mayo",
            "junio",
            "julio",
            "agosto",
            "septiembre",
            "octubre",
            "noviembre",
            "diciembre",
        ),
        "en": (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
    }
    language = language if language in weekdays else "en"
    weekday = weekdays[language][local_now.weekday()]
    month = months[language][local_now.month - 1]
    clock = local_now.strftime("%H:%M")
    if language == "pt":
        date_text = f"Hoje é {weekday}, {local_now.day} de {month} de {local_now.year}"
        time_text = f"A hora local é {clock}"
        suffix = f"em {country} (fuso horário {time_zone})"
        return (
            f"{date_text} e são {clock}, {suffix}."
            if asks_date and asks_time
            else (f"{date_text}, {suffix}." if asks_date else f"{time_text}, {suffix}.")
        )
    if language == "es":
        date_text = f"Hoy es {weekday}, {local_now.day} de {month} de {local_now.year}"
        time_text = f"La hora local es {clock}"
        suffix = f"en {country} (zona horaria {time_zone})"
        return (
            f"{date_text} y son las {clock}, {suffix}."
            if asks_date and asks_time
            else (f"{date_text}, {suffix}." if asks_date else f"{time_text}, {suffix}.")
        )
    date_text = f"Today is {weekday}, {month} {local_now.day}, {local_now.year}"
    time_text = f"The local time is {clock}"
    suffix = f"in {country} ({time_zone})"
    return (
        f"{date_text}, and the time is {clock} {suffix}."
        if asks_date and asks_time
        else (f"{date_text} {suffix}." if asks_date else f"{time_text} {suffix}.")
    )


def _direct_courtesy_response(
    raw_text: str,
    recipient_name: str = "",
) -> Optional[str]:
    """Responde cumplidos/agradecimientos directos sin ocupar SQL, RAG u Ollama."""
    text = " ".join((raw_text or "").strip().lower().split())
    if not text:
        return None
    display_name = " ".join(str(recipient_name or "").strip().split())
    try:
        uuid.UUID(display_name)
        display_name = ""
    except (ValueError, AttributeError):
        pass
    if display_name.lower() in {"desconocido", "unknown", "-"}:
        display_name = ""
    language = agent._detect_user_language(raw_text)
    greeting_name = f", {display_name}" if display_name else ""
    greeting_terms = {
        "hola",
        "hola como estas",
        "hola cómo estás",
        "buenos dias",
        "buenos días",
        "buenas tardes",
        "buenas noches",
        "olá",
        "ola",
        "bom dia",
        "boa tarde",
        "boa noite",
        "good morning",
        "good afternoon",
        "good evening",
        "hello",
        "hi",
    }
    if text.rstrip("!?., ") in greeting_terms:
        greetings = {
            "pt": f"Olá{greeting_name}! É um prazer cumprimentá-lo. Como posso ajudar?",
            "en": f"Hello{greeting_name}! It is a pleasure to greet you. How can I help?",
            "es": f"¡Hola{greeting_name}! Es un placer saludarte. ¿En qué puedo ayudarte?",
        }
        return greetings.get(language, greetings["en"])
    if _looks_like_question_or_request(text):
        return None
    if any(
        term in text
        for term in (
            "obrigado",
            "obrigada",
            "boa explicação",
            "boa explicacao",
            "muito bom",
        )
    ):
        return "Muito obrigado. Fico satisfeito por a explicação ter sido útil."
    if any(
        term in text
        for term in (
            "thank you",
            "thanks",
            "good explanation",
            "great explanation",
            "well explained",
        )
    ):
        return "Thank you. I am glad the explanation was helpful."
    if any(
        term in text
        for term in (
            "gracias",
            "buena explicación",
            "buena explicacion",
            "muy buena",
            "bien explicado",
        )
    ):
        return "Muchas gracias. Me alegra que la explicación haya sido útil."
    return None


def _is_external_information_query(raw_text: str) -> bool:
    """Separa consultas externas actuales de conocimiento operativo de trabajo."""
    text = " ".join((raw_text or "").strip().lower().split())
    external_terms = (
        "tiempo",
        "tempo",
        "temperatura",
        "temperature",
        "clima",
        "pronostico",
        "pronóstico",
        "meteorologia",
        "meteorología",
        "weather",
        "forecast",
        "previsão",
        "previsao",
        "noticias",
        "news",
        "resultado deportivo",
        "precio actual",
        "cotizacion",
        "cotización",
    )
    current_officeholder = bool(
        re.search(
            r"\b(?:quem|qui[eé]n|who|qual)\s+(?:[eé]|es|is|ser[aá])\s+(?:o |a |el |la |the )?"
            r"(?:presidente|president|primeiro[- ]ministro|primer ministro|prime minister|"
            r"governador|gobernador|governor|prefeito|alcalde|mayor|ceo|diretor executivo|"
            r"director ejecutivo)\b",
            text,
            flags=re.IGNORECASE,
        )
    )
    from app.agent.semantic_text import is_current_officeholder_question

    return current_officeholder or is_current_officeholder_question(raw_text) or any(term in text for term in external_terms)


def _auto_reply_rejection_reason(candidate: dict) -> Optional[str]:
    # Final authorization barrier immediately before generation/sending. Never
    # trust cached/routed candidate fields alone: re-read the original payload.
    # chat-question suggestions do not use this auto-reply pipeline.
    payload = (
        candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    )
    if _payload_has_learning_only_destination(payload):
        return "contenido_solo_aprendizaje"
    if not _payload_has_talk_with_agent(payload):
        return "talk_with_agent_no_autorizado"

    message = (candidate.get("message") or "").strip()
    response_requested = _payload_requests_agent_response(payload, message)
    if not response_requested:
        return "contenido_solo_aprendizaje"

    fingerprint = (candidate.get("fingerprint") or "").strip()
    if not fingerprint:
        return "sin_fingerprint"
    if _auto_reply_seen(fingerprint):
        return "fingerprint_ya_respondido"
    if candidate.get("generated_by_ia"):
        return "mensaje_generado_por_ia"

    channel_id = (candidate.get("channel_id") or "").strip()
    sender_resource = str(candidate.get("sender_resource") or "")
    sender_name = str(candidate.get("sender_name") or "")
    can_reply_direct = bool(
        candidate.get("is_direct") and candidate.get("reply_resource")
    )
    if not message:
        return "mensaje_vacio"
    if not channel_id and not can_reply_direct:
        return "sin_destino_para_responder"
    if (
        _candidate_is_learning_only(candidate)
        and not can_reply_direct
        and not response_requested
    ):
        return "respuesta_citada_solo_aprendizaje"

    # Con identidad explícita configurada, Chat.resourceTable es la fuente de verdad. Así
    # una mención textual dentro de un canal ajeno no provoca una respuesta.
    has_configured_recipient_identity = bool(
        (settings.SOLIDSET_LOGIN_RESOURCE_ID or "").strip()
        or (settings.SOLIDSET_RESOURCE_ID or "").strip()
    )
    if (
        has_configured_recipient_identity
        and not candidate.get("agent_resource_id")
        and not candidate.get("addressed_to_agent")
    ):
        return "destino_no_incluye_agente"
    mentioned = _message_mentions_agent(message)
    active_followup = _has_active_auto_reply_followup(candidate)
    kind_is_conversational = bool(candidate.get("kind_reply_eligible", True))
    if (
        not kind_is_conversational
        and not response_requested
        and not _looks_like_question_or_request(message)
        and not mentioned
        and not active_followup
    ):
        return f"evento_sin_peticion:{candidate.get('message_kind') or 'desconocido'}"
    if (
        not candidate.get("addressed_to_agent")
        and not response_requested
        and not mentioned
        and not _looks_like_question_or_request(message)
        and not active_followup
    ):
        return "no_es_pregunta_peticion_o_continuacion"
    if (
        not candidate.get("agent_resource_id")
        and (not settings.SOLIDSET_AUTO_REPLY_ALLOW_SELF)
        and _is_self_sender(sender_resource=sender_resource, sender_name=sender_name)
    ):
        return "remitente_es_recurso_del_agente"

    if settings.SOLIDSET_AUTO_REPLY_REQUIRE_MENTION:
        if (
            not candidate.get("addressed_to_agent")
            and not mentioned
            and not active_followup
        ):
            return "mencion_o_destino_directo_requerido"

    return None


def _payload_has_talk_with_agent(payload: dict[str, Any]) -> bool:
    chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    resource_table = _get_payload_value(chat, "resourceTable", "ResourceTable")
    participants = resolve_resource_table(resource_table)
    if participants.agent_recipient_ids:
        return True
    # Legacy preview payloads may include only the explicit AI destination and
    # omit the sender row/sequence; that is still an authorization signal.
    if isinstance(resource_table, list):
        return any(
            isinstance(row, dict)
            and str(row.get("type") or row.get("Type") or "") == "2"
            and normalize_bool(row.get("talkWithAgent") or row.get("TalkWithAgent"))
            for row in resource_table
        )
    return False


def _payload_has_learning_only_destination(payload: dict[str, Any]) -> bool:
    """Support legacy type=3 learning-only destinations."""
    chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    resource_table = _get_payload_value(chat, "resourceTable", "ResourceTable")
    tables = [resource_table, _get_payload_value(chat, "destiny", "Destiny")]
    framework_destiny = payload.get("FrameworkDestiny")
    if isinstance(framework_destiny, dict):
        tables.append(framework_destiny.get("dests") or framework_destiny.get("Dests"))
    return any(
        isinstance(row, dict)
        and str(row.get("type") or row.get("Type") or "") == "3"
        and (
            normalize_bool(row.get("talkWithAgent") or row.get("TalkWithAgent"))
            or str(row.get("kind") or row.get("Kind") or "") == "3"
        )
        for table in tables if isinstance(table, list)
        for row in table
    )


def _payload_requests_agent_response(payload: dict[str, Any], raw_text: str) -> bool:
    """Apply SolidSET's explicit QuestionType response contract.

    Type 1 has priority and type 3 is an explicit request. Type 0 is
    unclassified: an explicit ``talkWithAgent=true`` destination authorizes
    a response even without ``?`` (for example ``34 + 25``). Type 2 and
    missing/unknown values remain learning-only.
    """
    chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    question_type = _get_payload_value(chat, "questionType", "QuestionType")
    try:
        normalized_type = int(question_type)
    except (TypeError, ValueError):
        return False
    if normalized_type in {1, 3}:
        return True
    text = str(raw_text or "").strip()
    return normalized_type == 0 and (
        "?" in text
        or (
            _payload_has_talk_with_agent(payload)
            and (
                _local_arithmetic_response(text) is not None
                or _looks_like_question_or_request(text)
            )
        )
    )


def _selected_agent_resource_ids(candidate: dict) -> list[str]:
    """Selecciona únicamente los recursos destinatarios del mensaje dirigido."""
    payload = (
        candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    )
    chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    chat_lower = {str(key).lower(): value for key, value in chat.items()}
    resource_table = chat_lower.get("resourcetable")
    framework_destiny = payload.get("FrameworkDestiny")
    has_framework_destiny = isinstance(framework_destiny, dict) and bool(
        framework_destiny.get("dests") or framework_destiny.get("Dests")
    )
    selected_from_payload = payload.get("SelectedAgentResourceIds")
    if (
        isinstance(selected_from_payload, list)
        and selected_from_payload
        and not has_framework_destiny
    ):
        return list(dict.fromkeys(
            normalize_uuid(value) for value in selected_from_payload if normalize_uuid(value)
        ))
    participants = resolve_resource_table(resource_table)
    selected: list[str] = []
    if isinstance(resource_table, list):
        explicit_type_two = {
            normalize_uuid(
                {str(key).lower(): value for key, value in row.items()}.get("idresource")
                or {str(key).lower(): value for key, value in row.items()}.get("resource")
            )
            for row in resource_table
            if isinstance(row, dict)
            and str(row.get("type") or row.get("Type") or "") == "2"
            and normalize_bool(row.get("talkWithAgent") or row.get("TalkWithAgent"))
        }
        selected = [item for item in participants.agent_recipient_ids if item in explicit_type_two]
    tables = [resource_table]
    has_chat_agent_rows = isinstance(resource_table, list) and any(
        isinstance(row, dict)
        and str(row.get("type") or row.get("Type") or "") in {"2", "3"}
        for row in resource_table
    )
    if not has_chat_agent_rows:
        if isinstance(chat_lower.get("destiny"), list):
            tables.append(chat_lower["destiny"])
    framework_rows = (
        framework_destiny.get("dests") or framework_destiny.get("Dests")
        if isinstance(framework_destiny, dict) else None
    )
    if not has_chat_agent_rows and isinstance(framework_rows, list):
        tables.append(framework_rows)
    sender_resource = normalize_uuid(
        candidate.get("sender_resource")
        or (payload.get("FrameworkSender") or {}).get("resource")
        if isinstance(payload.get("FrameworkSender"), dict)
        else candidate.get("sender_resource")
    )
    # Legacy payloads may omit sequence and sender rows but still identify
    # explicit AI destinations with type=2/3 and talkWithAgent, or kind=2.
    for table_index, table in enumerate(tables):
        if not isinstance(table, list):
            continue
        for row in table:
            if not isinstance(row, dict):
                continue
            lowered = {str(key).lower(): value for key, value in row.items()}
            destination_type = str(lowered.get("type") or lowered.get("kind") or "")
            has_talk_flag = normalize_bool(lowered.get("talkwithagent"))
            is_framework_destiny = table is framework_rows
            is_agent = destination_type == "2" and (
                has_talk_flag or (is_framework_destiny and destination_type == "2")
            )
            if not is_agent:
                continue
            resource_id = normalize_uuid(
                lowered.get("idresource") or lowered.get("resource")
            )
            if resource_id and resource_id != sender_resource and resource_id not in selected:
                selected.append(resource_id)
    if not selected and sender_resource:
        channels = chat_lower.get("channels")
        is_private_self_chat = isinstance(channels, list) and any(
            isinstance(channel, dict)
            and str(channel.get("channelKind") or channel.get("kind") or "") == "1"
            for channel in channels
        )
        if is_private_self_chat:
            selected.append(sender_resource)
    return selected


def _human_reply_destination(candidate: dict) -> dict[str, str]:
    """Resuelve el emisor canónico (sequence=0/talkWithAgent=false)."""
    payload = (
        candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    )
    chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    chat_lower = {str(key).lower(): value for key, value in chat.items()}
    resource_table = chat_lower.get("resourcetable")
    sender_resource = str(candidate.get("sender_resource") or "").strip()
    participants = resolve_resource_table(resource_table)
    canonical_sender = (
        participants.sender_resource_id if participants.valid else sender_resource
    )
    if isinstance(resource_table, list):
        for destination in resource_table:
            if not isinstance(destination, dict):
                continue
            lowered = {str(key).lower(): value for key, value in destination.items()}
            resource = str(
                lowered.get("idresource") or lowered.get("resource") or ""
            ).strip()
            login = str(lowered.get("idlogin") or lowered.get("login") or "").strip()
            if not resource or resource.casefold() != canonical_sender.casefold():
                continue
            return {
                "resource": resource,
                "login": login,
                "resource_name": str(
                    lowered.get("username") or lowered.get("resourcename") or ""
                ).strip(),
            }
    return {
        "resource": canonical_sender,
        "login": str(candidate.get("sender_login") or "").strip(),
        "resource_name": str(candidate.get("sender_name") or "").strip(),
    }


def _selected_agent_chat_destination(
    candidate: dict, agent_resource_id: str
) -> dict[str, str]:
    """Conserva nombre/login del destino IA marcado con talkWithAgent."""
    payload = (
        candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    )
    chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    chat_lower = {str(k).lower(): v for k, v in chat.items()}
    for collection_name in ("destiny", "resourcetable"):
        destinations = chat_lower.get(collection_name)
        if not isinstance(destinations, list):
            continue
    destinations = {str(k).lower(): v for k, v in chat.items()}.get("resourcetable")
    if isinstance(destinations, list):
        for destination in destinations:
            if not isinstance(destination, dict):
                continue
            lowered = {str(key).lower(): value for key, value in destination.items()}
            resource = str(
                lowered.get("idresource") or lowered.get("resource") or ""
            ).strip()
            if resource.lower() != agent_resource_id.lower():
                continue
            return {
                "resource": resource,
                "login": str(
                    lowered.get("idlogin") or lowered.get("login") or ""
                ).strip(),
                "resource_name": str(lowered.get("resourcename") or "").strip(),
            }
    return {"resource": agent_resource_id, "login": "", "resource_name": ""}


def _agent_visible_name(configured_agent: dict[str, Any]) -> str:
    """Construye la identidad pública del agente desde el nombre de su login."""
    resource_id = str(configured_agent.get("IDResource") or "").strip()
    full_name = str(configured_agent.get("FullName") or "").strip()
    fallback = str(configured_agent.get("Name") or resource_id).strip()
    identity = full_name or fallback or resource_id
    return f"{identity}".strip()


def _payload_participant_resource_ids(candidate: dict) -> list[str]:
    payload = (
        candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    )
    chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    chat_lower = {str(key).lower(): value for key, value in chat.items()}
    participants: list[str] = []
    for collection_name in ("resourcetable", "destiny"):
        resources = chat_lower.get(collection_name)
        if not isinstance(resources, list):
            continue
        for resource in resources:
            if isinstance(resource, dict):
                lowered = {str(key).lower(): value for key, value in resource.items()}
                value = lowered.get("idresource") or lowered.get("resource")
                if value:
                    participants.append(str(value).strip())
    return list(dict.fromkeys(value for value in participants if value))


def _candidate_session_id(candidate: dict) -> uuid.UUID:
    payload = (
        candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    )
    sender = (
        payload.get("FrameworkSender")
        if isinstance(payload.get("FrameworkSender"), dict)
        else {}
    )
    candidates = (
        payload.get("IDSession"),
        sender.get("Session"),
        sender.get("session"),
        sender.get("IDSession"),
        candidate.get("chat_id"),
    )
    for value in candidates:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            continue
    stable_scope = (
        f"{candidate.get('channel_id')}|{candidate.get('sender_resource')}|"
        f"{candidate.get('reply_resource')}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, f"solidset-agent-session:{stable_scope}")


def _route_candidates_to_selected_agents(candidates: list[dict]) -> list[dict]:
    """Genera una ejecución por agente activo, seleccionado y asignado al canal."""
    routed: list[dict] = []
    for candidate in candidates:
        if candidate.get("generated_by_ia"):
            continue
        channel_id = str(candidate.get("channel_id") or "").strip()
        selected = _selected_agent_resource_ids(candidate)
        if not channel_id or not selected:
            continue
        instance = get_solidset_instance(
            code=str(candidate.get("solidset_instance_code") or "") or None,
            source_ip=None,
        )
        if not instance or not instance.get("DataAPI"):
            print("⚠️ Agente omitido: instância sem SolidSET Data API configurada")
            continue
        try:
            ensure_payload_agent_workroom_assignments(channel_id, selected)
            configured_agents = get_active_agents_for_workroom(
                channel_id, selected, instance.get("ID")
            )
        except (ValueError, psycopg.Error) as exc:
            print(
                f"⚠️ No se pudo resolver agentes seleccionados para {channel_id}: {exc}"
            )
            continue
        verified_mappings: dict[str, str] = {}
        for configured_agent in configured_agents:
            selected_resource_id = str(configured_agent["IDResource"])
            expected_agent_id = configured_agent.get("IDAgentResource")
            try:
                verification = verify_and_sync_solidset_agent_mapping(
                    selected_resource_id, expected_agent_id, instance
                )
            except SolidSETDataAPIError as exc:
                # A timeout/5xx means the authoritative source could not be
                # checked; it does not prove that the active relation was
                # removed. Preserve the already synchronized mapping so a
                # transient Data API outage does not silence the agent.
                cached_agent_id = str(expected_agent_id or "").strip()
                if cached_agent_id:
                    verified_mappings[selected_resource_id.lower()] = cached_agent_id
                    print(
                        "⚠️ Validação ao vivo de SysResource2Agent indisponível; "
                        "usando relação local ativa previamente sincronizada "
                        f"IDHumanResource={selected_resource_id} "
                        f"IDAgentResource={cached_agent_id}: {exc}"
                    )
                else:
                    print(
                        "⚠️ Agente omitido: validação ao vivo indisponível e sem "
                        "relação local sincronizada "
                        f"IDHumanResource={selected_resource_id}: {exc}"
                    )
                continue
            except (ValueError, pymssql.Error, psycopg.Error, RuntimeError) as exc:
                print(
                    "⚠️ Agente omitido: no se pudo verificar SysResource2Agent "
                    f"IDHumanResource={selected_resource_id}: {exc}"
                )
                continue
            verified_agent_id = str(verification.get("IDAgentResource") or "").strip()
            if not verification.get("verified") or not verified_agent_id:
                print(
                    "⚠️ Agente omitido: no existe relación activa en dbo.SysResource2Agent "
                    f"para IDHumanResource={selected_resource_id}"
                )
                continue
            verified_mappings[str(selected_resource_id).lower()] = verified_agent_id
        for configured_agent in configured_agents:
            agent_resource_id = str(configured_agent["IDResource"])
            cached_agent_resource_id = str(
                configured_agent.get("IDAgentResource") or ""
            ).strip()
            solidset_agent_resource_id = verified_mappings.get(
                agent_resource_id.lower(), ""
            )
            if cached_agent_resource_id != solidset_agent_resource_id:
                print(
                    "🔄 Identidad IA actualizada antes de responder "
                    f"human={agent_resource_id} "
                    f"previous={cached_agent_resource_id or '-'} "
                    f"current={solidset_agent_resource_id or '-'}"
                )
            if not solidset_agent_resource_id:
                print(
                    "⚠️ Agente omitido: no existe relación activa en dbo.SysResource2Agent "
                    f"para IDHumanResource={agent_resource_id}"
                )
                continue
            routed_candidate = dict(candidate)
            human_destination = _human_reply_destination(candidate)
            agent_chat_destination = _selected_agent_chat_destination(
                candidate, agent_resource_id
            )
            agent_chat_resource_name = str(
                agent_chat_destination.get("resource_name") or ""
            ).strip()
            if not agent_chat_resource_name:
                configured_resource_name = str(
                    configured_agent.get("Name") or "Agente IA"
                ).strip()
                agent_chat_resource_name = (
                    configured_resource_name
                    if configured_resource_name.lower().endswith("[ia]")
                    else f"{configured_resource_name} [IA]"
                )
            try:
                private_knowledge = get_agent_knowledge(agent_resource_id, channel_id)
            except (ValueError, psycopg.Error) as exc:
                print(
                    f"⚠️ Conocimiento privado no disponible para {agent_resource_id}: {exc}"
                )
                private_knowledge = ""
            try:
                reinforcement = get_agent_reinforcement_context(
                    agent_resource_id, channel_id
                )
            except (ValueError, psycopg.Error):
                reinforcement = ""
            routed_candidate.update(
                {
                    "fingerprint": f"{candidate.get('fingerprint')}:{agent_resource_id}",
                    "agent_resource_id": agent_resource_id,
                    "agent_identity_id": solidset_agent_resource_id,
                    "agent_configuration_id": str(configured_agent.get("ID") or ""),
                    "agent_name": _agent_visible_name(configured_agent),
                    "agent_session_id": str(_candidate_session_id(candidate)),
                    "agent_knowledge": private_knowledge,
                    "agent_reinforcement": reinforcement,
                    "addressed_to_agent": True,
                    # Una vez que SolidSET seleccionó al agente, su respuesta debe
                    # invertir la conversación: agente autenticado -> autor original.
                    # La detección inicial de ``is_direct`` solo conoce la identidad
                    # global configurada y puede no reconocer agentes dinámicos.
                    "is_direct": bool(human_destination.get("resource")),
                    "reply_resource": human_destination.get("resource", ""),
                    "reply_login": human_destination.get("login", ""),
                    "reply_resource_name": human_destination.get("resource_name", ""),
                    "agent_chat_resource_name": agent_chat_resource_name,
                    "agent_chat_login": agent_chat_destination.get("login", ""),
                    "reply_destiny_inverted": True,
                }
            )
            routed.append(routed_candidate)
    return routed


def _invoke_orchestrator_for_instance(instance_code: str, **kwargs: Any) -> str:
    if not instance_code:
        return orchestrator.invoke(**kwargs)
    instance = get_solidset_instance(code=instance_code, source_ip=None)
    if not instance or not instance.get("DataAPI"):
        raise RuntimeError(
            "A instância SolidSET não tem uma SolidSET Data API configurada."
        )
    with solidset_sql_instance_context(instance):
        return orchestrator.invoke(**kwargs)


def _learn_agent_interaction(
    *,
    agent_resource_id: str,
    channel_id: str,
    session_id: str,
    user_text: str,
    response_text: str,
) -> None:
    """Guarda aprendizaje etiquetado; nunca queda visible para otro agente."""
    from app.agent.semantic_text import mentions_public_role

    if _is_external_information_query(user_text) or mentions_public_role(user_text):
        return
    if not agent_learning_enabled(agent_resource_id, "system"):
        return
    digest = hashlib.sha256(
        f"{agent_resource_id}|{channel_id}|{session_id}|{user_text}|{response_text}".encode(
            "utf-8"
        )
    ).hexdigest()[:32]
    agent.sistema_aprendizaje.aprender_actividad(
        Actividad(
            id=f"agent_turn_{digest}",
            recurso_humano_id=agent_resource_id,
            canal_id=channel_id,
            tipo="agent_interaction",
            descripcion=f"Consulta: {user_text}\nRespuesta: {response_text}",
            timestamp=datetime.now(),
            metadatos={
                "agent_resource_id": agent_resource_id,
                "session_id": session_id,
                "source": "solidset_multi_agent",
            },
        )
    )


def _is_relative_temporal_assertion(raw_text: str) -> bool:
    """Evita convertir hechos relativos y caducos en conocimiento permanente."""
    text = " ".join(str(raw_text or "").strip().lower().split())
    return bool(
        re.search(
            r"\b(?:hoy|hoje|today|ayer|ontem|yesterday|mañana|amanhã|tomorrow)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _learn_global_user_fact(
    *,
    resource_id: str,
    workroom_id: str,
    fact: str,
    origin: str,
    solidset_instance_id: str = "",
) -> bool:
    """Indexa un hecho humano compartido sin compartir su perfil conductual."""
    normalized = " ".join(str(fact or "").split()).strip()
    if not normalized or _is_relative_temporal_assertion(normalized):
        return False
    digest = hashlib.sha256(
        f"{resource_id}|{workroom_id}|{normalized.casefold()}".encode("utf-8")
    ).hexdigest()[:32]
    return bool(
        agent.sistema_aprendizaje.aprender_actividad(
            Actividad(
                id=f"global_fact_{digest}",
                recurso_humano_id=resource_id,
                canal_id=workroom_id or "solidset_global",
                tipo="global_user_fact",
                descripcion=f"Hecho proporcionado por un recurso humano: {normalized}",
                timestamp=datetime.now(),
                metadatos={
                    "source": USER_ASSERTION_SOURCE,
                    "knowledge_scope": "global_shared",
                    "human_authored": True,
                    "learning_origin": origin,
                    "solidset_instance_id": solidset_instance_id,
                },
            )
        )
    )


def _learn_direct_agent_assertion(candidate: dict[str, Any]) -> bool:
    """Persiste una afirmación del recurso en el ámbito del agente seleccionado."""
    assertion = _sanitize_auto_reply_input(str(candidate.get("message") or ""))
    agent_resource_id = str(candidate.get("agent_resource_id") or "").strip()
    channel_id = str(candidate.get("channel_id") or "").strip()
    if (
        not assertion
        or not agent_resource_id
        or _is_relative_temporal_assertion(assertion)
    ):
        return False
    try:
        saved = save_agent_knowledge(
            {
                "IDResource": agent_resource_id,
                "IDWorkRoom": channel_id or None,
                "Title": "Hecho enseñado directamente al agente",
                "KnowledgeText": assertion,
                "Source": USER_ASSERTION_SOURCE,
                "active": True,
            }
        )
        # Se indexa antes de finalizar el trabajo para que el mensaje siguiente
        # pueda recuperar el hecho aunque llegue inmediatamente.
        if not saved.get("WasExisting"):
            indexed = agent.sistema_aprendizaje.aprender_conocimiento_agente(saved)
            if not indexed:
                raise RuntimeError("No se pudo indexar el conocimiento en Qdrant")
        _learn_global_user_fact(
            resource_id=agent_resource_id,
            workroom_id=channel_id,
            fact=assertion,
            origin="direct_agent_message",
            solidset_instance_id=str(candidate.get("solidset_instance_id") or ""),
        )
        print(
            f"🧠 Afirmación aprendida por agente resource={agent_resource_id} "
            f"workroom={channel_id or '-'} knowledge_id={saved.get('ID')}",
            flush=True,
        )
        return True
    except Exception as exc:
        print(
            f"⚠️ No se pudo aprender la afirmación dirigida al agente: {exc}", flush=True
        )
        return False


def _candidate_qualifies_for_auto_reply(candidate: dict) -> bool:
    return _auto_reply_rejection_reason(candidate) is None


async def _process_auto_replies(
    candidates: list[dict],
    *,
    preview_only: bool = False,
    _already_routed: bool = False,
    _finalize_status: bool = True,
    _preview_responses: list[dict[str, Any]] | None = None,
) -> int | list[dict[str, Any]]:
    # Cover routing as well as generation. Child tasks share the outer lease.
    if _already_routed or not candidates:
        return await _process_auto_replies_impl(
            candidates, preview_only=preview_only, _already_routed=_already_routed,
            _finalize_status=_finalize_status,
            _preview_responses=_preview_responses,
        )
    async with async_interactive_work("auto-reply-routing"):
        return await _process_auto_replies_impl(
            candidates, preview_only=preview_only, _already_routed=_already_routed,
            _finalize_status=_finalize_status,
            _preview_responses=_preview_responses,
        )


async def _process_auto_replies_impl(
    candidates: list[dict],
    *,
    preview_only: bool = False,
    _already_routed: bool = False,
    _finalize_status: bool = True,
    _preview_responses: list[dict[str, Any]] | None = None,
) -> int | list[dict[str, Any]]:
    print(
        f"🤖 Iniciando procesamiento de auto-respuesta; candidatos={len(candidates)}",
        flush=True,
    )
    response_request_id = str(
        next(
            (
                item.get("response_request_id")
                for item in candidates
                if item.get("response_request_id")
            ),
            "",
        )
    )
    if not preview_only and not settings.SOLIDSET_AUTO_REPLY_ENABLED:
        _update_response_status(
            response_request_id, "failed", error="La auto-respuesta está desactivada."
        )
        return 0
    if not preview_only and not settings.SOLIDSET_USER_ACTIONS_ENABLED:
        _update_response_status(
            response_request_id,
            "failed",
            error="El envío de acciones a SolidSET está desactivado.",
        )
        print(
            "⚠️ Auto-reply SOLIDSET activo en config, pero SOLIDSET_USER_ACTIONS_ENABLED=false. No se enviarán respuestas."
        )
        return 0

    _update_response_status(response_request_id, "processing")
    if not _already_routed:
        candidates = await asyncio.to_thread(_route_candidates_to_selected_agents, candidates)
    print(
        f"🤖 Enrutamiento de auto-respuesta completado; ejecuciones={len(candidates)}",
        flush=True,
    )
    if response_request_id and not candidates:
        _update_response_status(response_request_id, "completed", response_count=0)
    # Una selección explícita de SolidSET prevalece sobre el límite histórico
    # de una sola autorrespuesta, manteniendo un techo defensivo por mensaje.
    max_replies = min(
        10,
        max(1, settings.SOLIDSET_AUTO_REPLY_MAX_PER_CYCLE, len(candidates)),
    )
    if not _already_routed:
        unique_candidates: list[dict] = []
        unique_fingerprints: set[str] = set()
        for candidate in candidates:
            fingerprint = str(candidate.get("fingerprint") or "").strip()
            if not fingerprint or fingerprint in unique_fingerprints:
                continue
            unique_fingerprints.add(fingerprint)
            unique_candidates.append(candidate)
            if len(unique_candidates) >= max_replies:
                break
        # Register every participant before any task can finish, so status
        # aggregation cannot mistake the first reply for the whole request.
        for candidate in unique_candidates:
            _update_response_status(
                response_request_id, "queued",
                agent_resource_id=str(candidate.get("agent_identity_id") or candidate.get("agent_resource_id") or ""),
                agent_name=str(candidate.get("agent_name") or ""),
            )
        # Cada gemelo genera y envía de forma independiente. ``gather`` no
        # conserva un orden de entrega: responde primero quien termina antes.
        results = await asyncio.gather(
            *(
                _process_auto_replies(
                    [candidate],
                    preview_only=preview_only,
                    _already_routed=True,
                    _finalize_status=False,
                    _preview_responses=_preview_responses,
                )
                for candidate in unique_candidates
            ),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, Exception)]
        for candidate, result in zip(unique_candidates, results):
            if isinstance(result, Exception):
                # Catch failures outside the send path as well. Do not publish
                # raw exception messages which may contain provider data.
                _update_response_status(
                    response_request_id, "failed",
                    agent_resource_id=str(candidate.get("agent_identity_id") or candidate.get("agent_resource_id") or ""),
                    agent_name=str(candidate.get("agent_name") or ""),
                    error=f"La ejecución del agente falló ({type(result).__name__}).",
                )
                print(f"⚠️ Ejecución paralela de agente fallida: {type(result).__name__}", flush=True)
        if preview_only:
            flattened = [
                payload
                for result in results
                if isinstance(result, list)
                for payload in result
            ]
            return flattened
        completed = sum(
            int(result)
            for result in results
            if not isinstance(result, Exception) and isinstance(result, int)
        )
        if response_request_id:
            _update_response_status(
                response_request_id,
                "failed" if failures else "completed" if completed > 0 or not unique_candidates else "failed",
                error=None
                if not failures and (completed > 0 or not unique_candidates)
                else "Ningún agente pudo enviar la respuesta.",
                response_count=completed,
            )
        return completed
    sent = 0
    queued_for_delivery = 0
    preview_payloads: list[dict[str, Any]] = []
    local_seen = set()

    for candidate in candidates:
        if sent >= max_replies:
            break
        fingerprint = (candidate.get("fingerprint") or "").strip()
        if not fingerprint or fingerprint in local_seen:
            continue
        local_seen.add(fingerprint)

        rejection_reason = _auto_reply_rejection_reason(candidate)
        if rejection_reason is not None:
            if rejection_reason == "contenido_solo_aprendizaje":
                learned = await asyncio.to_thread(
                    _learn_direct_agent_assertion, candidate
                )
                if learned:
                    sent += 1
                    _update_response_status(
                        response_request_id,
                        "learned",
                        agent_resource_id=status_agent_id,
                        agent_name=agent_name,
                        response_count=sent,
                    )
                if not learned and _is_relative_temporal_assertion(
                    str(candidate.get("message") or "")
                ):

                    print(
                        "🕒 Afirmación temporal relativa no persistida; "
                        "se resolverá con el reloj de la instancia",
                        flush=True,
                    )
            print(
                "ℹ️ Candidato de auto-respuesta descartado por filtros "
                f"reason={rejection_reason} "
                f"addressed={bool(candidate.get('addressed_to_agent'))} "
                f"direct={bool(candidate.get('is_direct'))} "
                f"sender_resource={candidate.get('sender_resource', '-')} "
                f"sender={candidate.get('sender_name', 'desconocido')} "
                f"message={str(candidate.get('message') or '')[:120]!r}"
            )
            continue

        incoming_text = _sanitize_auto_reply_input(str(candidate.get("message") or ""))
        channel_id = (candidate.get("channel_id") or "").strip()
        is_direct = bool(candidate.get("is_direct"))
        reply_resource = str(candidate.get("reply_resource") or "").strip()
        reply_login = str(
            candidate.get("reply_login") or candidate.get("sender_login") or ""
        ).strip()
        visibility_level = int(candidate.get("visibility_level", 1))
        meeting_id = str(candidate.get("meeting_id") or "").strip()
        meeting_code = str(candidate.get("meeting_code") or "").strip()
        message_kind = str(candidate.get("message_kind") or "ChatMessage")
        message_category = str(candidate.get("message_category") or "chat")
        importance = int(candidate.get("importance", 0))
        message_metadata = {
            "chat_id": candidate.get("chat_id"),
            "quoted_chat_id": candidate.get("quoted_chat_id"),
            "quoted_message": candidate.get("quoted_message"),
            "quoted_sender_resource": candidate.get("quoted_sender_resource"),
            "recipient_count": int(candidate.get("recipient_count", 0)),
            "importance": importance,
            "agent_resource_id": candidate.get("agent_resource_id"),
            "agent_name": candidate.get("agent_name"),
            "agent_knowledge": candidate.get("agent_knowledge"),
            "agent_reinforcement": candidate.get("agent_reinforcement"),
            "workroom_id": channel_id,
            "meeting_id": meeting_id,
            "meeting_code": meeting_code,
            "country_code": candidate.get("country_code") or "PT",
            "locale": candidate.get("locale") or "pt-PT",
            "time_zone": candidate.get("time_zone") or "Europe/Lisbon",
            "solidset_instance_id": candidate.get("solidset_instance_id"),
            "solidset_instance_code": candidate.get("solidset_instance_code"),
            "current_reference_time": datetime.now(ZoneInfo(candidate.get("time_zone") or "Europe/Lisbon")).isoformat(),
            "system_utc_time": datetime.utcnow().isoformat()
        }

        if not incoming_text or (not channel_id and not reply_resource):
            continue

        conversation_scope = reply_resource if is_direct else channel_id
        agent_resource_id = str(candidate.get("agent_resource_id") or "").strip()
        agent_identity_id = str(candidate.get("agent_identity_id") or "").strip()
        agent_name = str(candidate.get("agent_name") or agent_resource_id).strip()
        relevant_agent_knowledge = ""
        if agent_resource_id:
            try:
                relevant_agent_knowledge = await asyncio.to_thread(
                    agent.sistema_aprendizaje.consultar_conocimiento_agente,
                    incoming_text,
                    agent_resource_id=agent_resource_id,
                    canal_id=channel_id,
                    min_score=settings.BUSINESS_RAG_MIN_SCORE,
                )
            except Exception as exc:
                print(
                    "⚠️ No se pudo preseleccionar conocimiento privado "
                    f"para enrutamiento: {exc}"
                )
        message_metadata["agent_relevant_knowledge"] = relevant_agent_knowledge
        print(
            "🧠 Conocimiento privado preseleccionado "
            f"resource={agent_resource_id or '-'} workroom={channel_id or '-'} "
            f"found={bool(relevant_agent_knowledge)} chars={len(relevant_agent_knowledge)}"
        )
        status_agent_id = agent_identity_id or agent_resource_id
        _update_response_status(
            response_request_id,
            "processing",
            agent_resource_id=status_agent_id,
            agent_name=agent_name,
        )
        conversation_id = str(
            candidate.get("chat_id")
            or candidate.get("agent_session_id")
            or conversation_scope
        )
        session_id = (
            f"solidset:{candidate.get('solidset_instance_id') or 'unscoped'}:"
            f"agent:{agent_resource_id}:room:{channel_id}:"
            f"conversation:{conversation_id}"
        )
        user_id = str(
            candidate.get("sender_resource")
            or candidate.get("sender_name")
            or settings.SOLIDSET_LOGIN_USERNAME
            or "solidset.agent"
        ).strip()
        learning_only = _candidate_is_learning_only(
            candidate
        ) and not _payload_requests_agent_response(
            candidate.get("payload")
            if isinstance(candidate.get("payload"), dict)
            else {},
            incoming_text,
        )
        from app.services.openai_direct import answer_direct
        try:
            response_text = await asyncio.to_thread(
                answer_direct, incoming_text, message_metadata, session_id
            )
        except Exception as exc:
            _update_response_status(
                response_request_id, "failed",
                agent_resource_id=status_agent_id, agent_name=agent_name,
                error=f"La llamada al modelo falló ({type(exc).__name__}).",
            )
            raise
        if response_text is None:
            response_text = (
                _learning_acknowledgement(incoming_text)
                if is_direct and learning_only
                else _direct_courtesy_response(
                    incoming_text,
                    str(candidate.get("sender_name") or ""),
                )
                if is_direct
                else None
            )
        if response_text is None:
            response_text = _local_temporal_response(
                incoming_text,
                time_zone=str(candidate.get("time_zone") or "Europe/Lisbon"),
                locale=str(candidate.get("locale") or "pt-PT"),
                country_code=str(candidate.get("country_code") or "PT"),
            )
        if response_text is None:
            response_text = _local_arithmetic_response(incoming_text)
        if response_text is None:
            response_text = _weather_location_prompt(incoming_text)
        if response_text is not None:
            _update_response_status(
                response_request_id,
                "thinking",
                agent_resource_id=status_agent_id,
                agent_name=agent_name,
            )
        if response_text is None:
            try:
                # La intención actual prevalece sobre recuerdos recuperados. Una
                # memoria (incluso relevante) no debe impedir verificar noticias,
                # clima, precios o titulares de cargos que pueden haber cambiado.
                external_query = _is_external_information_query(incoming_text)
                if external_query:
                    _update_response_status(
                        response_request_id,
                        "searching",
                        agent_resource_id=status_agent_id,
                        agent_name=agent_name,
                    )
                allowed_tools = (
                    {"google_web_search"}
                    if external_query
                    else {"query_sql_server", "get_db_schema"}
                )
                print(
                    f"🤖 Generando auto-respuesta con LLM channel={channel_id} "
                    f"target={'direct:' + reply_resource if is_direct else 'channel:' + channel_id} "
                    f"provider={settings.LLM_PROVIDER} model={settings.MODEL_NAME} "
                    f"base={settings.LLM_BASE_URL or settings.OLLAMA_BASE_URL} route="
                    f"{'external_web' if external_query else 'work_sql_rag'}"
                )
                _update_response_status(
                    response_request_id,
                    "thinking",
                    agent_resource_id=status_agent_id,
                    agent_name=agent_name,
                )
                response_text = await asyncio.to_thread(
                    _invoke_orchestrator_for_instance,
                    str(candidate.get("solidset_instance_code") or ""),
                    session_id=session_id,
                    user_text=incoming_text,
                    user_id=user_id,
                    # Aunque la respuesta sea dirigida, el workRoom sigue siendo
                    # el contexto funcional donde nació la conversación.
                    canal_id=channel_id,
                    meeting_id=meeting_id or None,
                    meeting_code=meeting_code or None,
                    message_kind=message_kind,
                    message_category=message_category,
                    message_metadata=message_metadata,
                    tool_allowlist=allowed_tools,
                    auto_reply_mode=True,
                )
            except Exception as exc:
                _update_response_status(
                    response_request_id,
                    "failed",
                    agent_resource_id=status_agent_id,
                    agent_name=agent_name,
                    error=str(exc),
                )
                print(
                    f"⚠️ Error generando auto-respuesta para canal {channel_id}: {exc}"
                )
                continue

        response_text = (response_text or "").strip()
        if not _is_safe_auto_reply_output(response_text):
            response_text = (
                "No pude procesar correctamente tu mensaje en este momento. "
                "Por favor, inténtalo de nuevo en unos instantes."
            )

        # Request-local collector shared by this request's concurrent agents.
        # Capture the validated final text before payload construction can fail.
        if preview_only and _preview_responses is not None:
            _preview_responses.append({
                "AgentResourceId": status_agent_id,
                "AgentName": agent_name,
                "Response": response_text,
            })

        try:
            _update_response_status(
                response_request_id,
                "thinking" if preview_only else "sending",
                agent_resource_id=status_agent_id,
                agent_name=agent_name,
            )
            print(
                f"📤 {'Preparando preview' if preview_only else 'Enviando auto-respuesta a SolidSET'} "
                f"base={candidate.get('solidset_base_url') or '-'} "
                f"agent_resource={agent_resource_id} meeting={meeting_id or '-'}",
                flush=True,
            )
            send_result = await asyncio.to_thread(
                solidset_send_chat_message.invoke,
                {
                    "canal_id": channel_id,
                    "mensaje": f"{response_text}",
                    "confirm": True,
                    "recurso_id": reply_resource if is_direct else None,
                    "recurso_login_id": reply_login if is_direct else None,
                    "visibility_level": visibility_level,
                    "kind": 7,
                    "importance": importance,
                    "meeting_id": meeting_id or None,
                    "meeting_code": meeting_code or None,
                    "meeting_mirror_general": bool(candidate.get("meeting_active")),
                    "generated_by_ia": True,
                    "agent_resource_id": agent_resource_id,
                    "agent_identity_id": agent_identity_id,
                    "agent_chat_resource_name": candidate.get(
                        "agent_chat_resource_name"
                    ),
                    "agent_chat_login_id": candidate.get("agent_chat_login"),
                    "human_chat_resource_name": candidate.get("reply_resource_name"),
                    "solidset_base_url": candidate.get("solidset_base_url"),
                    "preview_only": preview_only,
                    "question_chat_id": int(candidate.get("chat_id") or 0) or None,
                },
            )
            send_result_text = str(send_result)
            if preview_only:
                preview_payload = json.loads(send_result_text)
                if not isinstance(preview_payload, dict):
                    raise ValueError("El preview no devolvió un objeto de payload válido.")
                preview_payloads.append(preview_payload)
                sent += 1
                continue
            if send_result_text.startswith("✅"):
                sent += 1
                _update_response_status(
                    response_request_id,
                    "completed",
                    agent_resource_id=status_agent_id,
                    agent_name=agent_name,
                    response_count=sent,
                )
                _remember_auto_reply_fingerprint(fingerprint)
                _remember_auto_reply_followup(candidate)
                print(
                    f"🤖 Auto-reply enviado channel={channel_id} "
                    f"visibility={visibility_level} "
                    f"importance={importance} "
                    f"meeting={meeting_code or '-'} "
                    f"source_kind={message_kind} reply_kind=ChatMessage(7) "
                    f"sender={candidate.get('sender_name', 'desconocido')}"
                )
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            touch_agent_session,
                            candidate.get("agent_session_id"),
                            agent_resource_id,
                            channel_id,
                        ),
                        timeout=5,
                    )
                except Exception as exc:
                    print(
                        "⚠️ Respuesta enviada, pero no se pudo actualizar la "
                        f"sesión del agente: {exc}",
                        flush=True,
                    )
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            _learn_agent_interaction,
                            agent_resource_id=agent_resource_id,
                            channel_id=channel_id,
                            session_id=session_id,
                            user_text=incoming_text,
                            response_text=response_text,
                        ),
                        timeout=15,
                    )
                except Exception as exc:
                    print(
                        "⚠️ Respuesta enviada, pero no se pudo guardar su "
                        f"aprendizaje: {exc}",
                        flush=True,
                    )
            elif send_result_text.startswith("🕒"):
                # La respuesta ya está generada y persistida en la cola de
                # entrega. Se considera trabajo aceptado para que el worker de
                # respuestas no vuelva a ejecutar el LLM ni cree duplicados.
                queued_for_delivery += 1
                _update_response_status(
                    response_request_id,
                    "queued",
                    agent_resource_id=status_agent_id,
                    agent_name=agent_name,
                    error=send_result_text,
                    response_count=sent,
                )
                _remember_auto_reply_fingerprint(fingerprint)
                print(
                    f"📮 Auto-reply pendiente de entrega channel={channel_id}",
                    flush=True,
                )
            else:
                _update_response_status(
                    response_request_id,
                    "failed",
                    agent_resource_id=status_agent_id,
                    agent_name=agent_name,
                    error=send_result_text,
                    response_count=sent,
                )
                print(
                    f"⚠️ Auto-reply no enviado en canal {channel_id}: {send_result_text}"
                )
        except Exception as exc:
            _update_response_status(
                response_request_id,
                "failed",
                agent_resource_id=status_agent_id,
                agent_name=agent_name,
                error=str(exc),
                response_count=sent,
            )
            print(
                f"⚠️ Error enviando auto-respuesta a SOLIDSET (canal {channel_id}): {exc}"
            )

        if response_request_id and not preview_only and _finalize_status:
            final_status = "completed"
        if sent > 0:
            current_status = load(response_request_id)
            if current_status and current_status.get("status") == "learned":
                final_status = "learned"
        elif not candidates:
            final_status = "completed"
        elif queued_for_delivery:
            final_status = "queued"
        else:
            final_status = "failed"

        _update_response_status(
            response_request_id,
            final_status,
            error=None
            if sent > 0 or not candidates or queued_for_delivery
            else "Ningún agente pudo enviar la respuesta.",
            response_count=sent,
        )

    return preview_payloads if preview_only else sent + queued_for_delivery


def _get_payload_value(payload: Optional[dict[str, Any]], *keys: str) -> Any:
    """Obtiene un valor de un objeto FrameworkMessage sin depender del casing."""
    if not isinstance(payload, dict):
        return None
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    lowered = {str(key).lower(): value for key, value in payload.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return value
    return None
