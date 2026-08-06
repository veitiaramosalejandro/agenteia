import uuid
import hashlib
import json
import re
import time
import redis
from collections import Counter
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import pymssql
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Distance, VectorParams
from langchain_ollama import OllamaEmbeddings

from app.config import settings
from app.system.schema import (
    RecursoHumano, RecursoMaterial, Canal, Actividad, ContextoUsuario
)


class SistemaAprendizaje:
    """
    Sistema que aprende la dinámica de la plataforma de trabajo por canales.
    """
    
    def __init__(self):
        embedding_model = (settings.EMBEDDING_MODEL_NAME or "").strip() or "nomic-embed-text"
        self.embedding_model = embedding_model
        self.embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=embedding_model
        )
        self._embeddings_enabled = True
        self._embeddings_disabled_reason = None
        self.redis_cache = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.qdrant = QdrantClient(url=settings.VECTOR_DB_URL)
        self.collection = settings.VECTOR_COLLECTION_NAME
        self.sql_retry_stats = {
            "connect_retries": 0,
            "query_retries": 0,
            "connect_by_context": {},
            "query_by_context": {},
            "last_retry_at": None,
        }
        self._ensure_collection()

    def _embed_query_safe(self, text: str, context: str) -> Optional[List[float]]:
        """Genera embedding con protección para evitar bloqueos repetidos si Ollama falla."""
        if not self._embeddings_enabled:
            return None
        normalized = " ".join((text or "").strip().split())
        cache_key = (
            f"{settings.EMBEDDING_REDIS_CACHE_PREFIX}:"
            f"{getattr(self, 'embedding_model', settings.EMBEDDING_MODEL_NAME)}:"
            f"{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"
        )
        cache = getattr(self, "redis_cache", None)
        if settings.EMBEDDING_CACHE_ENABLED and cache is not None:
            try:
                cached = cache.get(cache_key)
                if cached:
                    return [float(value) for value in json.loads(cached)]
            except (redis.RedisError, ValueError, TypeError, json.JSONDecodeError) as exc:
                print(f"⚠️ Cache de embeddings Redis no disponible ({context}): {exc}")
        try:
            vector = self.embeddings.embed_query(normalized)
            if settings.EMBEDDING_CACHE_ENABLED and cache is not None:
                try:
                    cache.setex(
                        cache_key,
                        max(1, settings.EMBEDDING_CACHE_TTL_SECONDS),
                        json.dumps(vector, separators=(",", ":")),
                    )
                except redis.RedisError as exc:
                    print(f"⚠️ No se pudo guardar embedding en Redis ({context}): {exc}")
            return vector
        except Exception as e:
            self._embeddings_enabled = False
            self._embeddings_disabled_reason = str(e)
            print(f"⚠️ Embeddings deshabilitados temporalmente por error en Ollama ({context}): {e}")
            return None

    def _increment_retry_metric(self, metric_key: str, context_key: str):
        """Incrementa contadores de reintento SQL para observabilidad básica."""
        self.sql_retry_stats[metric_key] = self.sql_retry_stats.get(metric_key, 0) + 1
        by_context_key = "connect_by_context" if metric_key == "connect_retries" else "query_by_context"
        by_context = self.sql_retry_stats.get(by_context_key, {})
        by_context[context_key] = by_context.get(context_key, 0) + 1
        self.sql_retry_stats[by_context_key] = by_context
        self.sql_retry_stats["last_retry_at"] = datetime.now().isoformat()

    def get_sql_retry_stats(self) -> Dict[str, Any]:
        return dict(self.sql_retry_stats)

    def reset_sql_retry_stats(self) -> Dict[str, Any]:
        previous = dict(self.sql_retry_stats)
        self.sql_retry_stats = {"connect_retries": 0, "query_retries": 0, "connect_by_context": {}, "query_by_context": {}, "last_retry_at": None}
        return previous

    def _ensure_collection(self):
        """Asegura que la colección de aprendizaje exista en Qdrant."""
        try:
            collections = self.qdrant.get_collections().collections
            collection_names = [c.name for c in collections]
            if self.collection not in collection_names:
                self.qdrant.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=768, distance=Distance.COSINE)
                )
                print(f"✅ Colección de aprendizaje creada: {self.collection}")
        except Exception as e:
            print(f"⚠️ Error asegurando colección '{self.collection}': {e}")

    def _normalize_learning_text(self, text: str) -> str:
        text = (text or "").lower().strip()
        text = re.sub(r"\s+", " ", text)
        return text

    def _looks_like_uuid(self, value: Optional[str]) -> bool:
        raw = (value or "").strip()
        if not raw:
            return False
        try:
            uuid.UUID(raw)
            return True
        except Exception:
            return False

    def _extract_topic_keywords(self, *texts: str, limit: int = 8) -> List[str]:
        stopwords = {
            "para", "por", "con", "sin", "como", "que", "del", "las", "los", "una", "uno",
            "este", "esta", "esto", "esas", "esos", "sobre", "desde", "porque", "cuando",
            "donde", "what", "this", "that", "user", "username", "agente", "respuesta",
            "mensaje", "canal", "chat", "usuario", "sistema", "hola", "buenas", "gracias",
        }
        words: List[str] = []
        for text in texts:
            words.extend(re.findall(r"[a-záéíóúñ0-9]{4,}", self._normalize_learning_text(text)))
        counts = Counter(word for word in words if word not in stopwords)
        return [word for word, _ in counts.most_common(limit)]

    def _build_learning_tags(self, user_id: str, canal_id: Optional[str], activity_type: str, topics: List[str]) -> Dict[str, Any]:
        return {
            "user_id": user_id,
            "canal_id": canal_id,
            "activity_type": activity_type,
            "topics": topics,
            "topics_text": ", ".join(topics),
        }

    def _connect_sql_with_retry(self, **kwargs):
        """Conexión robusta a SQL Server con reintentos para fallos transitorios."""
        last_error = None
        for attempt in range(1, kwargs.get("retries", 3) + 1):
            try:
                return pymssql.connect(
                    server=settings.SQL_SERVER_HOST,
                    user=settings.SQL_SERVER_USER,
                    password=settings.SQL_SERVER_PASSWORD,
                    database=settings.SQL_SERVER_DB,
                    timeout=kwargs.get("timeout", 10),
                )
            except Exception as e:
                last_error = e
                if attempt < kwargs.get("retries", 3):
                    self._increment_retry_metric("connect_retries", kwargs.get("context", "sql"))
                    delay = kwargs.get("base_delay_seconds", 1) * attempt
                    print(f"⚠️ Conexión SQL falló ({kwargs.get('context', 'sql')}) intento {attempt}/{kwargs.get('retries', 3)}: {e}. Reintentando en {delay}s...")
                    time.sleep(delay)
        raise last_error

    def _execute_with_retry(self, cursor, **kwargs):
        """Ejecuta una consulta SQL con reintentos para queries propensas a timeout."""
        last_error = None
        for attempt in range(1, kwargs.get("retries", 2) + 1):
            try:
                cursor.execute(kwargs["query"], kwargs.get("params", ()))
                return
            except Exception as e:
                last_error = e
                if attempt < kwargs.get("retries", 2):
                    self._increment_retry_metric("query_retries", kwargs.get("context", "sql_query"))
                    delay = kwargs.get("base_delay_seconds", 1) * attempt
                    print(f"⚠️ Consulta SQL falló ({kwargs.get('context', 'sql_query')}) intento {attempt}/{kwargs.get('retries', 2)}: {e}. Reintentando en {delay}s...")
                    time.sleep(delay)
        raise last_error

    def _fetch_one_with_fresh_connection_retry(
        self,
        query: str,
        params: tuple,
        context: str,
        retries: int = 2,
    ) -> Optional[Dict[str, Any]]:
        """Ejecuta una consulta de una fila reabriendo conexión en cada reintento."""
        last_error = None
        max_retries = max(1, retries)
        for attempt in range(1, max_retries + 1):
            try:
                with self._connect_sql_with_retry(context=f"{context}_connect") as conn:
                    with conn.cursor(as_dict=True) as cursor:
                        cursor.execute(query, params)
                        return cursor.fetchone()
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    self._increment_retry_metric("query_retries", context)
                    delay = attempt
                    print(
                        f"⚠️ Consulta SQL con reconexión falló ({context}) intento "
                        f"{attempt}/{max_retries}: {e}. Reintentando en {delay}s..."
                    )
                    time.sleep(delay)
        raise last_error

    def _resolve_user_identity(self, user_id: str) -> Dict[str, Optional[str]]:
        """Resuelve username/login/resource para aceptar user_id como Username o GUID."""
        raw_user = (user_id or "").strip()
        identity = {"input": raw_user, "username": raw_user, "login_id": None, "resource_id": None, "full_name": None, "display_name": None}
        if not raw_user:
            return identity
        try:
            row = self._fetch_one_with_fresh_connection_retry(
                query="""
                    SELECT TOP 1 sl.IDLogin, sl.Username, sl.FullName, sr.ResourceId, sr.DisplayName
                    FROM dbo.SysLogin sl WITH (NOLOCK)
                    LEFT JOIN dbo.SysResources sr WITH (NOLOCK) ON sr.ActiveIDLogin2Resource = sl.ActiveIDLogin2Resource
                    WHERE sl.Username = %s
                       OR sl.IDLogin = TRY_CONVERT(uniqueidentifier, %s)
                       OR sr.ResourceId = TRY_CONVERT(uniqueidentifier, %s)
                    ORDER BY CASE WHEN sl.Username = %s THEN 0 ELSE 1 END, sl.Username
                """,
                params=(raw_user, raw_user, raw_user, raw_user),
                context="resolve_user_identity_query",
                retries=2,
            )
            if row:
                identity.update({
                    "username": (row.get("Username") or raw_user).strip(),
                    "login_id": str(row.get("IDLogin") or "").strip() or None,
                    "resource_id": str(row.get("ResourceId") or "").strip() or None,
                    "full_name": (row.get("FullName") or "").strip() or None,
                    "display_name": (row.get("DisplayName") or "").strip() or None,
                })
        except Exception as e:
            print(f"⚠️ No se pudo resolver identidad de usuario '{raw_user}': {e}")
        return identity

    def _get_user_details_from_db(self, cursor: pymssql.Cursor, identity: Dict) -> Optional[Dict]:
        self._execute_with_retry(
            cursor,
            query="""
                SELECT TOP 1
                    sl.IDLogin,
                    sl.Username,
                    sl.FullName,
                    sl.ActiveIDLogin2Resource,
                    sr.ResourceId,
                    sr.DisplayName
                FROM dbo.SysLogin sl
                LEFT JOIN dbo.SysResources sr
                    ON sr.ActiveIDLogin2Resource = sl.ActiveIDLogin2Resource
                WHERE sl.Username = %s
                   OR sl.IDLogin = TRY_CONVERT(uniqueidentifier, %s)
                ORDER BY CASE WHEN sl.Username = %s THEN 0 ELSE 1 END, sl.Username
            """,
            params=(
                identity.get("username"),
                identity.get("login_id") or identity.get("input"),
                identity.get("username"),
            ),
            context="get_user_details"
        )
        return cursor.fetchone()

    def _get_user_role_from_db(self, cursor: pymssql.Cursor, identity: Dict, resource_guid: str) -> Optional[str]:
        """
        Obtiene el rol funcional del usuario según modelo SOLIDSET.

        Fuente principal (requerimiento funcional): SysResources.DisplayName.
        Fallback: catálogo SysRole cuando DisplayName no esté disponible.
        """
        try:
            # Fuente principal indicada por negocio: SysResources.DisplayName.
            self._execute_with_retry(
                cursor,
                query="""
                    SELECT TOP 1 sr.DisplayName
                    FROM dbo.SysResources sr WITH (NOLOCK)
                    INNER JOIN dbo.SysLogin sl WITH (NOLOCK)
                        ON sl.ActiveIDLogin2Resource = sr.ActiveIDLogin2Resource
                    WHERE sl.Username = %s
                       OR sl.IDLogin = TRY_CONVERT(uniqueidentifier, %s)
                       OR sr.ResourceId = TRY_CONVERT(uniqueidentifier, %s)
                    ORDER BY CASE WHEN sl.Username = %s THEN 0 ELSE 1 END, sl.Username
                """,
                params=(
                    identity.get("username"),
                    identity.get("login_id") or identity.get("input"),
                    resource_guid,
                    identity.get("username"),
                ),
                context="get_user_role_display_name",
            )
            row = cursor.fetchone()
            role_name = str(row.get("DisplayName") or "").strip() if row else ""
            if role_name:
                return role_name

            # Fallback técnico al catálogo de roles clásico.
            self._execute_with_retry(
                cursor,
                query="""
                    SELECT TOP 1 sr.Code
                    FROM dbo._SysRole_SysResource srs
                    INNER JOIN dbo.SysRole sr ON sr.IDActivityRole = srs.IDRole
                    WHERE srs.IDResource = TRY_CONVERT(uniqueidentifier, %s)
                    ORDER BY sr.ShareChat DESC, sr.ViewScheduler DESC, sr.Code
                """,
                params=(resource_guid,),
                context="get_user_role_fallback",
            )
            rol_row = cursor.fetchone()
            return str(rol_row.get("Code")).strip() if rol_row and rol_row.get("Code") else None
        except Exception:
            return None

    def _get_user_channels_from_db(self, cursor: pymssql.Cursor, identity: Dict) -> List[Dict]:
        self._execute_with_retry(
            cursor,
            query="""
                WITH user_rooms AS (
                    SELECT DISTINCT wr.IDWorkRoom, wr.Name, wr.Description, wr.Kind
                    FROM dbo.SysWorkRoomResource wrr
                    INNER JOIN dbo.SysWorkRoom wr ON wr.IDWorkRoom = wrr.IDWorkRoom
                    WHERE wrr.IDResource = TRY_CONVERT(uniqueidentifier, %s)
                    OR wrr.IDLogin = TRY_CONVERT(uniqueidentifier, %s)
                    OR EXISTS (SELECT 1 FROM dbo.SysLogin slu WHERE slu.IDLogin = wrr.IDLogin AND slu.Username = %s)
                ), room_members AS (
                    SELECT 
                        wrr.IDWorkRoom,
                        -- Reemplazar STRING_AGG con FOR XML PATH para evitar límite de 8000 bytes
                        STUFF((
                            SELECT DISTINCT ',' + COALESCE(CONVERT(varchar(max), sub.IDResource), CONVERT(varchar(max), sub.IDLogin))
                            FROM dbo.SysWorkRoomResource sub
                            WHERE sub.IDWorkRoom = wrr.IDWorkRoom
                            FOR XML PATH(''), TYPE
                        ).value('.', 'varchar(max)'), 1, 1, '') AS Members
                    FROM dbo.SysWorkRoomResource wrr
                    INNER JOIN user_rooms ur ON ur.IDWorkRoom = wrr.IDWorkRoom
                    GROUP BY wrr.IDWorkRoom
                )
                SELECT ur.IDWorkRoom, ur.Name, ur.Description, ur.Kind, rm.Members
                FROM user_rooms ur
                LEFT JOIN room_members rm ON rm.IDWorkRoom = ur.IDWorkRoom
                ORDER BY ur.Name
            """,
            params=(identity.get("resource_id"), identity.get("login_id"), identity.get("username")),
            context="get_user_channels"
        )
        return cursor.fetchall() or []

    def _get_user_activities_from_db(self, cursor: pymssql.Cursor, identity: Dict) -> List[Dict]:
        try:
            # Usar una fecha límite para limitar los resultados
            self._execute_with_retry(
                cursor,
                query="""
                    SELECT TOP 20 
                        c.IDChat2, 
                        c.Stamp, 
                        LEFT(c.RawMessage, 500) as RawMessage,
                        COALESCE(c.IDWorkRoom, c2w.IDWorkRoom) as IDChannel,
                        wr.Name as ChannelName
                    FROM dbo.SysChat2SysResource c2rsc WITH (NOLOCK)  -- Usar NOLOCK para evitar bloqueos
                    INNER JOIN dbo.SysChat c WITH (NOLOCK) ON c.IDChat2 = c2rsc.IDChat
                    LEFT JOIN dbo.SysChat2SysWorkRoom c2w WITH (NOLOCK) ON c2w.IDChat2 = c.IDChat2
                    LEFT JOIN dbo.SysWorkRoom wr WITH (NOLOCK) ON wr.IDWorkRoom = COALESCE(c.IDWorkRoom, c2w.IDWorkRoom)
                    WHERE (c2rsc.IDResource = TRY_CONVERT(uniqueidentifier, %s)
                    OR c2rsc.IDLogin = TRY_CONVERT(uniqueidentifier, %s)
                    OR EXISTS (
                        SELECT 1 
                        FROM dbo.SysLogin slu WITH (NOLOCK)
                        WHERE slu.IDLogin = c2rsc.IDLogin 
                        AND slu.Username = %s
                    ))
                    AND c.RawMessage IS NOT NULL 
                    AND LEN(c.RawMessage) > 0
                    AND c.Stamp >= DATEADD(month, -6, GETDATE())  -- Solo últimos 6 meses
                    ORDER BY c.Stamp DESC
                """,
                params=(identity.get("resource_id"), identity.get("login_id"), identity.get("username")),
                context="get_user_activities"
            )
            return cursor.fetchall() or []
        except Exception as e:
            print(f"⚠️ Fallback chat-actividad no disponible: {e}")
            return []

    def _get_user_resources_from_db(self, cursor: pymssql.Cursor, identity: Dict) -> List[Dict]:
        try:
            # Paso 1: Obtener IDs de chat del usuario
            self._execute_with_retry(
                cursor,
                query="""
                    SELECT TOP 50 c.IDChat2
                    FROM dbo.SysChat2SysResource c2rsc
                    INNER JOIN dbo.SysChat c ON c.IDChat2 = c2rsc.IDChat
                    WHERE c2rsc.IDResource = TRY_CONVERT(uniqueidentifier, %s)
                    OR c2rsc.IDLogin = TRY_CONVERT(uniqueidentifier, %s)
                    OR EXISTS (
                        SELECT 1 
                        FROM dbo.SysLogin slu 
                        WHERE slu.IDLogin = c2rsc.IDLogin 
                        AND slu.Username = %s
                    )
                    ORDER BY c.Stamp DESC
                """,
                params=(identity.get("resource_id"), identity.get("login_id"), identity.get("username")),
                context="get_user_resources_ids"
            )
            
            chat_ids = cursor.fetchall() or []
            if not chat_ids:
                return []
            
            # Construir lista de IDs para la segunda consulta
            id_list = ','.join([str(row['IDChat2']) for row in chat_ids])
            
            # Paso 2: Obtener recursos de esos chats
            self._execute_with_retry(
                cursor,
                query=f"""
                    SELECT DISTINCT TOP 30
                        c2r.RecordCode, 
                        c2r.RecordShortName,
                        COUNT(*) as UsageCount,
                        MAX(c2r.Stamp) as LastUsed
                    FROM dbo.SysChat2Record c2r
                    WHERE c2r.IDChat IN ({id_list})
                    AND c2r.RecordCode IS NOT NULL
                    GROUP BY c2r.RecordCode, c2r.RecordShortName
                    ORDER BY UsageCount DESC, LastUsed DESC
                """,
                params=(),
                context="get_user_resources_details"
            )
            return cursor.fetchall() or []
            
        except Exception as e:
            print(f"⚠️ Fallback recursos de chat no disponibles: {e}")
            return []

    def _get_user_resources_from_db_safe(self, cursor: pymssql.Cursor, identity: Dict) -> List[Dict]:
        """Versión tolerante a fallos temporales de SQL Server para no bloquear la ingesta completa."""
        try:
            return self._get_user_resources_from_db(cursor, identity)
        except Exception as e:
            print(f"⚠️ Se omiten recursos de chat por fallo temporal de SQL Server: {e}")
            return []

    # ============================================================
    # 1. OBTENER CONTEXTO DEL USUARIO
    # ============================================================
    
    def obtener_contexto_usuario(self, user_id: str) -> Optional[ContextoUsuario]:
        """Obtiene todo el contexto de un usuario para personalizar respuestas."""
        return self._build_user_context(user_id)

    def _build_user_context(self, user_id: str) -> Optional[ContextoUsuario]:
        """Construye el contexto completo de un usuario desde la base de datos."""
        try:
            identity = self._resolve_user_identity(user_id)
            with self._connect_sql_with_retry(context="build_user_context") as conn:
                with conn.cursor(as_dict=True) as cursor:
                    usuario_data = None
                    try:
                        usuario_data = self._get_user_details_from_db(cursor, identity)
                    except Exception as e:
                        print(f"⚠️ get_user_details temporalmente no disponible, usando fallback mínimo: {e}")

                    if not usuario_data:
                        fallback_username = (identity.get("username") or user_id or "usuario").strip()
                        fallback_name = (
                            identity.get("full_name")
                            or identity.get("display_name")
                            or fallback_username
                        )
                        usuario = RecursoHumano(
                            id=fallback_username,
                            nombre=fallback_name,
                            email="",
                            rol="operario",
                            departamento=None,
                            especialidades=["operario"],
                            canales=[]
                        )
                        return ContextoUsuario(
                            usuario=usuario,
                            canales_acceso=[],
                            actividades_recientes=[],
                            recursos_disponibles=[],
                            permisos=self._obtener_permisos_por_rol(usuario.rol)
                        )

                    user_login = (usuario_data.get("Username") or identity.get("username") or user_id or "").strip()
                    resource_guid = str(usuario_data.get("ResourceId") or identity.get("resource_id") or user_id)
                    rol = self._get_user_role_from_db(cursor, identity, resource_guid) or "operario"
                    nombre = (
                        usuario_data.get("FullName")
                        or usuario_data.get("DisplayName")
                        or identity.get("full_name")
                        or user_login
                    )

                    usuario = RecursoHumano(
                        id=user_login,
                        nombre=nombre,
                        email="",
                        rol=rol,
                        departamento=None,
                        especialidades=[rol] if rol else [],
                        canales=[]
                    )

                    canales_rows = self._get_user_channels_from_db(cursor, identity)
                    canales = []
                    for row in canales_rows:
                        canal_id = str(row.get("IDWorkRoom"))
                        miembros = [m.strip() for m in (row.get("Members") or "").split(',') if m.strip()]
                        canal = Canal(id=canal_id, nombre=str(row.get("Name") or f"Canal {canal_id[:8]}").strip(), descripcion=str(row.get("Description") or "Canal de trabajo").strip(), tipo=f"workroom_{row.get('Kind')}", recursos_humanos=miembros, recursos_materiales=[])
                        canales.append(canal)
                        usuario.canales.append(canal_id)

                    actividades_rows = self._get_user_activities_from_db(cursor, identity)
                    actividades = []
                    for a in actividades_rows:
                        descripcion = (a.get("RawMessage") or "").strip()
                        if a.get("RecordCode"):
                            descripcion = f"{descripcion} | Registro relacionado: {a.get('RecordCode')} {a.get('RecordShortName')}".strip()
                        actividades.append(Actividad(id=str(a.get("IDChat2")), recurso_humano_id=usuario.id, canal_id=str(a.get("IDChannel") or "general"), tipo="chat", descripcion=descripcion, timestamp=a.get("Stamp") or datetime.now(), metadatos={"channel_name": a.get("ChannelName"), "record_code": a.get("RecordCode")}))

                    recursos_rows = self._get_user_resources_from_db_safe(cursor, identity)
                    recursos_disponibles = [RecursoMaterial(id=m.get("RecordCode"), nombre=(m.get("RecordShortName") or m.get("RecordCode")), tipo="chat_record", canal_id=str(m.get("IDWorkRoom") or "general"), estado="disponible", especificaciones={"record_code": m.get("RecordCode")}) for m in recursos_rows if m.get("RecordCode")]

            return ContextoUsuario(
                usuario=usuario,
                canales_acceso=canales,
                actividades_recientes=actividades,
                recursos_disponibles=recursos_disponibles,
                permisos=self._obtener_permisos_por_rol(usuario.rol)
            )
        except Exception as e:
            print(f"❌ Error construyendo contexto de usuario: {e}")
            return None

    def analyze_reaction_patterns(
        self,
        user_text: str,
        agent_response: str,
        previous_user_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analiza patrones explícitos e implícitos de feedback/reacción del usuario."""
        normalized = self._normalize_learning_text(user_text)
        previous_normalized = self._normalize_learning_text(previous_user_text or "")

        negative_phrases = [
            "no entend", "no es lo que busco", "no es eso", "eso no", "no sirve", "está mal",
            "esta mal", "incorrect", "no quiero eso", "otra cosa", "no me refiero", "me equivoqué",
            "te equivoc", "corrige", "respuesta correcta",
        ]
        repetition_phrases = [
            "te lo repito", "te repito", "otra vez", "lo vuelvo a preguntar", "no respondiste",
        ]

        is_negative = any(phrase in normalized for phrase in negative_phrases)
        is_repeated = bool(previous_normalized) and normalized == previous_normalized
        if not is_repeated and previous_normalized:
            from difflib import SequenceMatcher
            is_repeated = SequenceMatcher(None, normalized, previous_normalized).ratio() >= 0.85
        is_repeated = is_repeated or any(phrase in normalized for phrase in repetition_phrases)

        if is_negative and is_repeated:
            signal = "correccion_y_repeticion"
        elif is_negative:
            signal = "feedback_negativo"
        elif is_repeated:
            signal = "pregunta_repetida"
        else:
            signal = "sin_senal"

        topics = self._extract_topic_keywords(user_text, agent_response, limit=5)
        confidence = 0.0
        if is_negative:
            confidence += 0.5
        if is_repeated:
            confidence += 0.4
        if topics:
            confidence += 0.1

        return {
            "signal": signal,
            "is_negative": is_negative,
            "is_repeated": is_repeated,
            "confidence": round(min(confidence, 1.0), 2),
            "topics": topics,
            "normalized_user_text": normalized,
            "normalized_previous_text": previous_normalized,
        }

    def registrar_feedback_usuario(
        self,
        user_id: str,
        canal_id: Optional[str],
        session_id: str,
        user_text: str,
        agent_response: str,
        corrected_response: Optional[str] = None,
        feedback_type: str = "explicit",
        reason: Optional[str] = None,
        previous_user_text: Optional[str] = None,
        implicit: bool = False,
    ) -> bool:
        """Guarda correcciones y feedback como ejemplos de entrenamiento."""
        try:
            identity = self._resolve_user_identity(user_id)
            usuario_nombre = identity.get("full_name") or identity.get("display_name") or identity.get("username") or user_id
            username = identity.get("username") or user_id
            reaction = self.analyze_reaction_patterns(user_text, agent_response, previous_user_text=previous_user_text)
            topics = reaction.get("topics") or self._extract_topic_keywords(user_text, agent_response, corrected_response or "", limit=5)

            tipo = "feedback_negativo" if reaction.get("is_negative") and not corrected_response else "correccion_usuario"
            descripcion = (
                f"Feedback de {usuario_nombre} ({username}) en canal {canal_id or 'general'}: "
                f"pregunta='{user_text[:250]}', respuesta_agente='{agent_response[:250]}', "
                f"respuesta_correcta='{(corrected_response or '')[:250]}', motivo='{reason or feedback_type}', "
                f"señal='{reaction.get('signal')}', repetida={reaction.get('is_repeated')}, negativa={reaction.get('is_negative')}"
            )

            actividad = Actividad(
                id=f"feedback_{hashlib.md5(f'{session_id}:{user_text}:{agent_response}:{corrected_response or ""}'.encode()).hexdigest()[:24]}",
                recurso_humano_id=username,
                canal_id=canal_id or "general",
                tipo=tipo,
                descripcion=descripcion,
                timestamp=datetime.now(),
                metadatos={
                    "source": "user_feedback",
                    "session_id": session_id,
                    "feedback_type": feedback_type,
                    "implicit": implicit,
                    "reason": reason,
                    "agent_response": agent_response,
                    "corrected_response": corrected_response,
                    "previous_user_text": previous_user_text,
                    "reaction": reaction,
                    "topics": topics,
                },
            )
            return self.aprender_actividad(actividad)
        except Exception as e:
            print(f"❌ Error registrando feedback de usuario: {e}")
            return False

    def actualizar_perfil_usuario(
        self,
        user_id: str,
        canal_id: Optional[str] = None,
        recent_user_text: Optional[str] = None,
        recent_agent_response: Optional[str] = None,
        feedback_summary: Optional[str] = None,
    ) -> bool:
        """Genera y persiste un snapshot del perfil dinámico del usuario."""
        try:
            contexto = self.obtener_contexto_usuario(user_id)
            identity = self._resolve_user_identity(user_id)
            username = identity.get("username") or user_id
            display_name = identity.get("full_name") or identity.get("display_name") or username

            topics = self._extract_topic_keywords(
                recent_user_text or "",
                recent_agent_response or "",
                feedback_summary or "",
                " ".join([a.descripcion for a in (contexto.actividades_recientes[:5] if contexto else [])]),
                limit=8,
            )

            canales = [c.nombre for c in (contexto.canales_acceso[:5] if contexto else [])]
            rol = contexto.usuario.rol if contexto else "desconocido"
            perfil_texto = (
                f"Perfil dinámico de {display_name} ({username}). Rol actual: {rol}. "
                f"Canales frecuentes: {', '.join(canales) if canales else 'sin_datos'}. "
                f"Temas detectados: {', '.join(topics) if topics else 'sin_temas'}. "
                f"Feedback -reciente: {feedback_summary or 'sin_feedback_reciente'}."
            )

            actividad = Actividad(
                id=f"profile_{hashlib.md5(f'{username}:{canal_id or "general"}:{perfil_texto}'.encode()).hexdigest()[:24]}",
                recurso_humano_id=username,
                canal_id=canal_id or "perfil_usuario",
                tipo="perfil_usuario",
                descripcion=perfil_texto,
                timestamp=datetime.now(),
                metadatos={
                    "source": "dynamic_profile",
                    "username": username,
                    "display_name": display_name,
                    "rol": rol,
                    "canales": canales,
                    "topics": topics,
                    "feedback_summary": feedback_summary,
                },
            )
            return self.aprender_actividad(actividad)
        except Exception as e:
            print(f"❌ Error actualizando perfil de usuario: {e}")
            return False

    def obtener_perfil_dinamico(self, user_id: str, canal_id: Optional[str] = None) -> str:
        """Recupera un resumen dinámico del usuario desde el aprendizaje persistido."""
        identity = self._resolve_user_identity(user_id)
        username = identity.get("username") or user_id
        query = f"perfil dinamico usuario {username} rol canales expertise feedback"
        aprendizaje = self.consultar_aprendizaje(query, canal_id=canal_id, limit=3)
        if aprendizaje.startswith("No hay"):
            return f"Usuario: {username}. Sin perfil dinámico aún."
        return aprendizaje
    
    def _obtener_permisos_por_rol(self, rol: str) -> List[str]:
        """Mapea roles a permisos específicos."""
        permisos_base = ["consultar_informacion"]
        permisos_rol = {
            "operario": ["ver_telemetria", "reportar_incidencias"],
            "supervisor": ["ver_telemetria", "ver_estadisticas", "asignar_tareas", "reportar_incidencias"],
            "ingeniero": ["ver_telemetria", "ver_estadisticas", "modificar_parametros", "diagnosticar"],
            "gerente": ["ver_todos", "ver_estadisticas", "generar_reportes", "consultar_informacion"],
            "mantenimiento": ["ver_telemetria", "diagnosticar", "programar_mantenimiento"]
        }
        return permisos_base + permisos_rol.get(rol.lower(), [])

    def obtener_recurso_id_por_nombre(self, nombre: str) -> Optional[str]:
        """Busca el ID de recurso por nombre visible en SysResources."""
        nombre = (nombre or "").strip()
        if not nombre:
            return None

        try:
            with self._connect_sql_with_retry(context="resource_by_name") as conn:
                with conn.cursor(as_dict=True) as cursor:
                    self._execute_with_retry(
                        cursor,
                        query="""
                            SELECT TOP 1 ResourceId, DisplayName FROM dbo.SysResources
                            WHERE DisplayName LIKE %s
                            ORDER BY CASE WHEN DisplayName = %s THEN 0 ELSE 1 END, DisplayName
                        """,
                        params=(f"%{nombre}%", nombre),
                        context="resource_by_name_query"
                    )
                    row = cursor.fetchone()
                    return str(row.get("ResourceId")).strip() if row and row.get("ResourceId") else None
        except Exception as e:
            print(f"⚠️ Error buscando recurso por nombre '{nombre}': {e}")
            return None

    # ============================================================
    # 2. OBTENER MENSAJES DE CHAT (VERSIÓN ULTRARÁPIDA)
    # ============================================================
    
    def obtener_mensajes_chat_desde_bd(
        self,
        user_id: str,
        canal_id: Optional[str] = None,
        limit: int = 8,
        offset: int = 0,
        sender_resource_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Obtiene mensajes recientes de chat desde BD con validación de membresía."""
        identity = self._resolve_user_identity(user_id)
        username = (identity.get("username") or user_id or "").strip()
        if not username:
            return []

        safe_limit = max(1, min(limit, 30))
        safe_offset = max(0, offset)
        
        query_parts = [
            """
            WITH user_channels AS (
                SELECT DISTINCT wrr.IDWorkRoom
                FROM dbo.SysWorkRoomResource wrr WITH (NOLOCK)
                LEFT JOIN dbo.SysLogin sl WITH (NOLOCK) ON sl.IDLogin = wrr.IDLogin
                WHERE wrr.IDResource = (SELECT TOP 1 ResourceId FROM dbo.SysResources WHERE ActiveIDLogin2Resource = (SELECT TOP 1 ActiveIDLogin2Resource FROM dbo.SysLogin WHERE Username = %s))
                   OR sl.Username = %s
            )
            SELECT TOP %s
                c.IDChat2,
                c.Stamp,
                c.RawMessage,
                c.IDWorkRoom AS IDChannel,
                wr.Name AS ChannelName,
                c.IDSenderResource AS SenderResourceId,
                COALESCE(sr.DisplayName, sl.FullName, sl.Username, 'Sin nombre') AS SenderDisplayName,
                COALESCE(sl.FullName, '') AS SenderFullName,
                COALESCE(sl.Username, '') AS SenderUsername
            FROM dbo.SysChat c WITH (NOLOCK)
            INNER JOIN dbo.SysWorkRoom wr WITH (NOLOCK) ON wr.IDWorkRoom = c.IDWorkRoom
            LEFT JOIN dbo.SysResources sr WITH (NOLOCK) ON sr.ResourceId = c.IDSenderResource
            LEFT JOIN dbo.SysLogin sl WITH (NOLOCK) ON sl.IDLogin = c.IDSender
            WHERE c.IDWorkRoom IN (SELECT IDWorkRoom FROM user_channels)
              AND c.RawMessage IS NOT NULL
              AND LEN(LTRIM(RTRIM(c.RawMessage))) > 0
            """
        ]
        params = [username, username, safe_limit + safe_offset]

        if canal_id:
            query_parts.append("AND c.IDWorkRoom = %s")
            params.append(canal_id)
        if sender_resource_id:
            query_parts.append("AND c.IDSenderResource = %s")
            params.append(sender_resource_id)

        query_parts.append("ORDER BY c.Stamp DESC, c.IDChat2 DESC")
        
        try:
            with self._connect_sql_with_retry(context="chat_messages_ultra") as conn:
                with conn.cursor(as_dict=True) as cursor:
                    self._execute_with_retry(cursor, query=" ".join(query_parts), params=tuple(params), context="chat_messages_ultra_query")
                    rows = cursor.fetchall() or []
            
            messages = [
                {
                    "chat_id": str(row.get("IDChat2")), "timestamp": row.get("Stamp"), "message": (row.get("RawMessage") or "").strip(),
                    "channel_id": str(row.get("IDChannel")), "channel_name": (row.get("ChannelName") or "").strip(),
                    "sender_resource_id": str(row.get("SenderResourceId")), "sender_display_name": (row.get("SenderDisplayName") or "").strip(),
                    "sender_full_name": (row.get("SenderFullName") or "").strip(), "sender_username": (row.get("SenderUsername") or "").strip(),
                }
                for row in rows if (row.get("RawMessage") or "").strip()
            ]
            return messages[safe_offset:safe_offset + safe_limit]
        except Exception as e:
            print(f"⚠️ Error obteniendo mensajes de chat desde BD: {e}")
            return []

    def obtener_usuarios_recurso_del_canal(self, user_id: str, canal_id: Optional[str], limit: int = 80) -> List[Dict[str, Any]]:
        """Obtiene usuarios recurso del canal de forma liviana y con validación de acceso."""
        identity = self._resolve_user_identity(user_id)
        if not user_id or not (canal_id or "").strip():
            return []

        try:
            with self._connect_sql_with_retry(context="channel_members") as conn:
                with conn.cursor(as_dict=True) as cursor:
                    self._execute_with_retry(
                        cursor,
                        query="""
                            SELECT TOP 1 1 AS allowed FROM dbo.SysWorkRoomResource req
                            WHERE req.IDWorkRoom = TRY_CONVERT(uniqueidentifier, %s)
                              AND (req.IDResource = TRY_CONVERT(uniqueidentifier, %s) OR req.IDLogin = TRY_CONVERT(uniqueidentifier, %s)
                                   OR EXISTS (SELECT 1 FROM dbo.SysLogin slu WHERE slu.IDLogin = req.IDLogin AND slu.Username = %s))
                        """,
                        params=(canal_id, identity.get("resource_id"), identity.get("login_id"), identity.get("username")),
                        context="channel_members_acl"
                    )
                    if not cursor.fetchone():
                        return []

                    self._execute_with_retry(
                        cursor,
                        query=f"""
                            SELECT TOP {max(1, min(limit, 120))}
                                wr.IDWorkRoom, wr.Name AS ChannelName, COALESCE(sr.ResourceId, wrr.IDResource) AS ResourceId,
                                COALESCE(sr.DisplayName, sl.FullName, sl.Username, 'Sin nombre') AS DisplayName,
                                sl.Username, sl.FullName, wrr.IDLogin
                            FROM dbo.SysWorkRoomResource wrr
                            INNER JOIN dbo.SysWorkRoom wr ON wr.IDWorkRoom = wrr.IDWorkRoom
                            INNER JOIN dbo.SysResources sr ON sr.ResourceId = wrr.IDResource
                            INNER JOIN dbo.SysLogin sl ON sl.ActiveIDLogin2Resource = sr.ActiveIDLogin2Resource
                            WHERE wrr.IDWorkRoom = TRY_CONVERT(uniqueidentifier, %s)
                            ORDER BY DisplayName
                        """,
                        params=(canal_id,),
                        context="channel_members_query"
                    )
                    return [
                        {
                            "channel_id": str(row.get("IDWorkRoom")), "channel_name": (row.get("ChannelName") or "").strip(),
                            "resource_id": str(row.get("ResourceId")), "display_name": (row.get("DisplayName") or "").strip(),
                            "username": (row.get("Username") or "").strip(), "full_name": (row.get("FullName") or "").strip(),
                            "login_id": str(row.get("IDLogin")),
                        }
                        for row in (cursor.fetchall() or [])
                    ]
        except Exception as e:
            print(f"⚠️ Error obteniendo usuarios recurso del canal: {e}")
            return []

    def obtener_contexto_chat_desde_bd(self, user_id: str, canal_id: Optional[str] = None, limit: int = 8) -> str:
        """Obtiene contexto reciente del chat directamente desde la base de datos."""
        rows = self.obtener_mensajes_chat_desde_bd(user_id=user_id, canal_id=canal_id, limit=limit)
        if not rows:
            return "No hay historial de chat reciente en la base de datos para este usuario."

        lines = [
            f"[{row.get('timestamp').strftime('%Y-%m-%d %H:%M:%S')}] ({(row.get('channel_name') or 'N/A')}) [{row.get('sender_display_name') or row.get('sender_username') or 'N/A'}] {row.get('message', '')[:180]}"
            for row in rows
        ]
        return "\n".join(lines) or "No hay mensajes de chat útiles para contexto en base de datos."

    def obtener_resumen_operativo_canal(self, user_id: str, canal_id: Optional[str], limit: int = 6) -> str:
        """Construye una vista operativa del canal actual (mensajes, miembros y señales aprendidas)."""
        if not user_id:
            return ""

        if not (canal_id or "").strip():
            return "No se recibió canal_id; no es posible generar resumen operativo del canal actual."

        mensajes = self.obtener_mensajes_chat_desde_bd(user_id=user_id, canal_id=canal_id, limit=max(3, min(limit, 12)))
        miembros = self.obtener_usuarios_recurso_del_canal(user_id=user_id, canal_id=canal_id, limit=12)
        aprendizaje = self.consultar_aprendizaje(
            query=f"eventos recientes notificaciones actividad canal {canal_id}",
            canal_id=canal_id,
            limit=2,
        )

        if not mensajes and not miembros:
            return "No se encontró actividad operativa reciente del canal en este momento."

        canal_nombre = "Canal actual"
        if mensajes:
            canal_nombre = mensajes[0].get("channel_name") or canal_nombre
        elif miembros:
            canal_nombre = miembros[0].get("channel_name") or canal_nombre

        lines: List[str] = [f"Canal: {canal_nombre} ({canal_id})"]

        if miembros:
            miembros_txt = ", ".join(
                [m.get("display_name") or m.get("username") or "sin_nombre" for m in miembros[:8]]
            )
            lines.append(f"Miembros activos/relevantes: {miembros_txt}")

        if mensajes:
            lines.append("Mensajes recientes:")
            for row in mensajes[:6]:
                ts = row.get("timestamp")
                ts_text = ts.strftime("%H:%M") if hasattr(ts, "strftime") else "--:--"
                autor = row.get("sender_display_name") or row.get("sender_username") or "N/A"
                lines.append(f"- [{ts_text}] {autor}: {(row.get('message') or '')[:160]}")

        if aprendizaje and "No hay conocimiento" not in aprendizaje:
            lines.append("Señales aprendidas del canal:")
            lines.append(aprendizaje)

        return "\n".join(lines)
    
    # ============================================================
    # 3. GENERAR CONTEXTO PARA EL AGENTE (en texto)
    # ============================================================
    
    def generar_contexto_agente(self, user_id: str) -> str:
        """Genera un texto de contexto para inyectar en el System Prompt del agente."""
        contexto = self.obtener_contexto_usuario(user_id)
        if not contexto:
            return "No se pudo obtener el contexto del usuario."
        
        texto = f"""
        === CONTEXTO DEL USUARIO ===
        Usuario: {contexto.usuario.nombre}, Rol: {contexto.usuario.rol}, Dept: {contexto.usuario.departamento or 'N/A'}
        """
        
        texto += "=== CANALES DE ACCESO ===\n"
        for canal in contexto.canales_acceso:
            texto += f"  - {canal.nombre} (ID: {canal.id[:8]}...): {len(canal.recursos_humanos)} miembros\n"
        
        texto += "\n=== ACTIVIDAD RECIENTE ===\n"
        for act in contexto.actividades_recientes[:5]:
            texto += f"  • {act.descripcion[:100]}... ({act.timestamp.strftime('%d/%m')})\n"

        perfil_dinamico = self.obtener_perfil_dinamico(user_id)
        if perfil_dinamico:
            texto += f"\n=== PERFIL DINÁMICO ===\n{perfil_dinamico}\n"
        
        texto += f"\n=== PERMISOS: {', '.join(contexto.permisos)} ===\n"
        return texto
    
    # ============================================================
    # 4. CONSULTAR DOCUMENTACIÓN TÉCNICA (RAG)
    # ============================================================

    def consultar_documentacion(self, query: str, limit: int = 3) -> str:
        """
        Busca en la base de conocimiento (Qdrant) documentos técnicos relevantes.
        Esta función es el núcleo del sistema RAG para documentación.
        """
        query_vector = self._embed_query_safe(query, context="consultar_documentacion_rag")
        if query_vector is None:
            return "" # Devolver vacío en lugar de un mensaje de error

        # ✅ Usar el método de búsqueda interno
        # No se aplica filtro para buscar en toda la documentación técnica
        resultados = self._search_aprendizaje(
            query_vector,
            query_filter=None,
            limit=limit
        )

        if not resultados:
            return ""

        # Formatear los resultados para el contexto del LLM
        seen_ids = set()
        formatted_results = []
        for hit in resultados:
            # Evitar duplicados si la búsqueda devuelve el mismo item
            if hit['id'] not in seen_ids:
                content = hit['payload'].get('page_content', '')
                source = hit['payload'].get('source', 'desconocido')
                source_url = hit['payload'].get('source_url', '')
                learned_at = hit['payload'].get('learned_at', '')
                provenance = f"Fuente: {source}"
                if source_url:
                    provenance += f"\nURL original: {source_url}"
                if learned_at:
                    provenance += f"\nAprendido el: {learned_at}"
                formatted_results.append(f"{provenance}\nContenido: {content[:400]}...")
                seen_ids.add(hit['id'])
            if len(formatted_results) >= limit:
                break
        
        return "\n---\n".join(formatted_results)

    # ============================================================
    # 4. APRENDER DE LAS ACTIVIDADES (RAG)
    # ============================================================
    
    def aprender_actividad(self, actividad: Actividad) -> bool:
        """Aprende de una actividad realizada por un usuario y la indexa en Qdrant."""
        try:
            metadata = actividad.metadatos or {}
            source_table = str(metadata.get("source_table") or "").strip().lower()
            source = str(metadata.get("source") or "").strip().lower()

            # Fast path para eventos de notificación/chat: evita consultas SQL por cada evento.
            is_realtime_event = source_table == "notificationapi" or source.startswith("chat_") or source.startswith("notification_")

            recurso_id = str(actividad.recurso_humano_id or "sistema").strip() or "sistema"
            usuario_nombre = str(metadata.get("sender_name") or recurso_id)
            usuario_rol = str(metadata.get("sender_role") or "sistema")

            # Solo intenta resolver contexto SQL cuando aporta valor y no es un ID técnico.
            should_resolve_context = (
                recurso_id != "sistema"
                and not is_realtime_event
                and not self._looks_like_uuid(recurso_id)
            )

            if should_resolve_context:
                contexto_usuario = self.obtener_contexto_usuario(recurso_id)
                if contexto_usuario:
                    usuario_nombre = contexto_usuario.usuario.nombre or usuario_nombre
                    usuario_rol = contexto_usuario.usuario.rol or usuario_rol

            texto_aprendizaje = f"Actividad por {usuario_nombre} ({usuario_rol}) en canal {actividad.canal_id}: {actividad.descripcion}"
            
            vector = self._embed_query_safe(texto_aprendizaje, context="aprender_actividad")
            if vector is None: return False
            
            point_id = str(uuid.UUID(hashlib.md5(texto_aprendizaje.encode()).hexdigest()))
            self.qdrant.upsert(
                collection_name=self.collection,
                points=[PointStruct(id=point_id, vector=vector, payload={**actividad.dict(), "page_content": texto_aprendizaje})]
            )
            return True
        except Exception as e:
            print(f"❌ Error aprendiendo actividad: {e}")
            return False
    
    def aprender_canal(self, canal: Canal) -> bool:
        """Aprende la estructura de un canal y la indexa en Qdrant."""
        try:
            texto_canal = f"Canal: {canal.nombre}. Descripción: {canal.descripcion}. Miembros: {len(canal.recursos_humanos)}."
            vector = self._embed_query_safe(texto_canal, context="aprender_canal")
            if vector is None: return False

            point_id = str(uuid.UUID(hashlib.md5(canal.id.encode()).hexdigest()))
            self.qdrant.upsert(
                collection_name=self.collection,
                points=[PointStruct(id=point_id, vector=vector, payload={**canal.dict(), "page_content": texto_canal, "source": "estructura_canal"})]
            )
            return True
        except Exception as e:
            print(f"❌ Error aprendiendo canal: {e}")
            return False
    
    # ============================================================
    # 5. CONSULTAR CONOCIMIENTO APRENDIDO (para el agente)
    # ============================================================
    
    def _search_aprendizaje(self, query_vector: List[float], 
                       query_filter: Optional[Dict[str, Any]] = None, 
                       limit: int = 10,
                       timeout: int = 30) -> List[Dict]:
        """
        Busca en Qdrant utilizando un filtro opcional y con timeout.
        
        Args:
            query_vector: Vector de consulta
            query_filter: Filtro opcional (ej: {"categoria": "torno"})
            limit: Número máximo de resultados
            timeout: Segundos de espera para la respuesta del servidor.
        
        Returns:
            Lista de resultados con payload
        """
        try:
            # Construir el filtro correctamente para v1.18.0
            filter_obj = None
            if query_filter:
                from qdrant_client.http import models
                
                # Si query_filter ya es un objeto Filter, usarlo directamente
                if isinstance(query_filter, models.Filter):
                    filter_obj = query_filter
                # Si es un diccionario, convertirlo
                elif isinstance(query_filter, dict):
                    # Verificar si tiene la estructura 'must'
                    if 'must' in query_filter:
                        # Ya tiene la estructura de Filter
                        conditions = []
                        for condition in query_filter['must']:
                            if isinstance(condition, dict):
                                conditions.append(
                                    models.FieldCondition(
                                        key=condition.get('key'),
                                        match=models.MatchValue(value=condition.get('match', {}).get('value'))
                                    )
                                )
                        filter_obj = models.Filter(must=conditions)
                    else:
                        # Convertir diccionario simple a Filter
                        conditions = []
                        for key, value in query_filter.items():
                            conditions.append(
                                models.FieldCondition(
                                    key=key,
                                    match=models.MatchValue(value=value)
                                )
                            )
                        filter_obj = models.Filter(must=conditions)
            
            # Compatibilidad entre versiones del cliente: query_points (nuevo) y search (legado)
            results = None
            query_points_error = None

            if hasattr(self.qdrant, "query_points"):
                try:
                    response = self.qdrant.query_points(
                        collection_name=self.collection,
                        query=query_vector,
                        limit=limit,
                        query_filter=filter_obj,
                        with_payload=True,
                        with_vectors=False,
                        score_threshold=0.0,
                        timeout=timeout,
                    )
                except TypeError:
                    # Algunas versiones no exponen todos los kwargs opcionales.
                    response = self.qdrant.query_points(
                        collection_name=self.collection,
                        query=query_vector,
                        limit=limit,
                        query_filter=filter_obj,
                    )
                except Exception as e:
                    query_points_error = e
                else:
                    results = response.points if hasattr(response, "points") else response

            if results is None and hasattr(self.qdrant, "search"):
                try:
                    results = self.qdrant.search(
                        collection_name=self.collection,
                        query_vector=query_vector,
                        limit=limit,
                        query_filter=filter_obj,
                        with_payload=True,
                        with_vectors=False,
                        score_threshold=0.0,
                        timeout=timeout,
                    )
                except TypeError:
                    # Fallback para firmas antiguas sin kwargs extra.
                    results = self.qdrant.search(
                        collection_name=self.collection,
                        query_vector=query_vector,
                        limit=limit,
                        query_filter=filter_obj,
                    )

            if results is None:
                if query_points_error is not None:
                    raise query_points_error
                raise AttributeError("El cliente Qdrant no expone 'query_points' ni 'search'.")
            
            # Convertir resultados a formato amigable
            formatted_results = []
            for result in results:
                formatted_results.append({
                    'id': result.id,
                    'score': result.score,
                    'payload': result.payload
                })
            
            return formatted_results
            
        except AttributeError as e:
            print(f"❌ Error: El cliente Qdrant no tiene el método 'search'")
            print(f"🔍 Versión instalada: {self._get_version()}")
            print("💡 Solución: Verifica la instalación de qdrant-client")
            return []
            
        except Exception as e:
            if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                print(f"⏳ Timeout en búsqueda de aprendizaje: la base de datos de vectores no respondió a tiempo (límite: {timeout}s).")
            else:
                print(f"❌ Error en búsqueda de aprendizaje: {e}")
                print(f"   Tipo: {type(e).__name__}")
            return []

    def _get_version(self):
        """Obtiene la versión de qdrant-client instalada."""
        try:
            from importlib.metadata import version
            return version("qdrant-client")
        except Exception:
            return "desconocida"

    def consultar_aprendizaje(self, query: str, canal_id: Optional[str] = None, limit: int = 3) -> str:
        """Consulta el conocimiento aprendido, opcionalmente filtrado por canal."""
        query_vector = self._embed_query_safe(query, context="consultar_aprendizaje")
        if query_vector is None: 
            return "No hay conocimiento previo."

        resultados = []
        
        # Búsqueda con filtro de canal
        if canal_id:
            # ✅ Crear el filtro correctamente para v1.18.0
            from qdrant_client.http import models
            filtro = models.Filter(
                must=[
                    models.FieldCondition(
                        key="canal_id",
                        match=models.MatchValue(value=canal_id)
                    )
                ]
            )
            resultados.extend(
                self._search_aprendizaje(
                    query_vector, 
                    query_filter=filtro,  # Pasar el objeto Filter
                    limit=limit
                )
            )
        
        # Búsqueda sin filtro (si no hay resultados con filtro o siempre)
        if not resultados or len(resultados) < limit:
            resultados.extend(
                self._search_aprendizaje(
                    query_vector, 
                    query_filter=None, 
                    limit=limit
                )
            )
        
        if not resultados:
            return "No hay conocimiento previo relacionado."

        seen_ids = set()
        formatted_results = []
        for hit in resultados:
            if hit['id'] not in seen_ids:
                content = hit['payload'].get('page_content', '')
                formatted_results.append(f"• {content[:300]}...")
                seen_ids.add(hit['id'])
            if len(formatted_results) >= limit:
                break
        
        return "📚 CONOCIMIENTO APRENDIDO RELACIONADO:\n" + "\n".join(formatted_results)
    
    # ============================================================
    # 6. SUGERIR COLABORADORES (basado en actividades pasadas)
    # ============================================================
    
    def sugerir_colaboradores(self, canal_id: str, tipo_actividad: str, limit: int = 5) -> List[Dict]:
        """Sugiere colaboradores basados en actividades similares en el pasado."""
        try:
            query = f"Actividad tipo {tipo_actividad} en canal {canal_id}"
            query_vector = self._embed_query_safe(query, context="sugerir_colaboradores")
            if query_vector is None: 
                return []

            # ✅ Usar el método corregido
            resultados = self._search_aprendizaje(
                query_vector, 
                query_filter=None,  # Sin filtro para buscar en todo
                limit=10
            )
            
            colaboradores = {}
            for hit in resultados:
                payload = hit.get('payload', {})
                if payload.get('source') == 'actividad_aprendida':
                    usuario_id = payload.get('recurso_humano_id')
                    if usuario_id:
                        colaboradores[usuario_id] = colaboradores.get(usuario_id, 0) + 1
            
            # Ordenar por relevancia y obtener detalles
            sorted_colabs = sorted(colaboradores.items(), key=lambda item: item[1], reverse=True)
            return [{"id": id, "score": score} for id, score in sorted_colabs[:limit]]
            
        except Exception as e:
            print(f"❌ Error sugiriendo colaboradores: {e}")
            return []
