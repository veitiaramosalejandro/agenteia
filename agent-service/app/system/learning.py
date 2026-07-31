import uuid
import hashlib
import time
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
        self.embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME
        )
        self._embeddings_enabled = True
        self._embeddings_disabled_reason = None
        self.qdrant = QdrantClient(url=settings.VECTOR_DB_URL)
        self.collection = settings.VECTOR_COLLECTION_NAME
        self.sql_retry_stats = {
            "connect_retries": 0,
            "query_retries": 0,
            "connect_by_context": {},
            "query_by_context": {},
            "last_retry_at": None,
        }
        self._primary_schema_available = False
        self._ensure_collection()

    def _embed_query_safe(self, text: str, context: str) -> Optional[List[float]]:
        """Genera embedding con protección para evitar bloqueos repetidos si Ollama falla."""
        if not self._embeddings_enabled:
            return None

        try:
            return self.embeddings.embed_query(text)
        except Exception as e:
            self._embeddings_enabled = False
            self._embeddings_disabled_reason = str(e)
            print(
                "⚠️ Embeddings deshabilitados temporalmente por error en Ollama "
                f"({context}): {e}"
            )
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
        """Devuelve métricas acumuladas de reintentos SQL."""
        return dict(self.sql_retry_stats)

    def reset_sql_retry_stats(self) -> Dict[str, Any]:
        """Reinicia métricas de reintentos SQL y devuelve el snapshot anterior."""
        previous = dict(self.sql_retry_stats)
        self.sql_retry_stats = {
            "connect_retries": 0,
            "query_retries": 0,
            "connect_by_context": {},
            "query_by_context": {},
            "last_retry_at": None,
        }
        return previous

    def _ensure_collection(self):
        """Asegura que la colección de aprendizaje exista en Qdrant."""
        try:
            collections = self.qdrant.get_collections()
            collection_names = [c.name for c in collections.collections]
            if self.collection not in collection_names:
                self.qdrant.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=768, distance=Distance.COSINE)
                )
                print(f"✅ Colección de aprendizaje creada: {self.collection}")
        except Exception as e:
            print(f"⚠️ Error asegurando colección '{self.collection}': {e}")

    def _connect_sql_with_retry(
        self,
        timeout: int = 10,
        retries: int = 3,
        base_delay_seconds: int = 1,
        context: str = "sql",
    ):
        """Conexión robusta a SQL Server con reintentos para fallos transitorios."""
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                return pymssql.connect(
                    server=settings.SQL_SERVER_HOST,
                    user=settings.SQL_SERVER_USER,
                    password=settings.SQL_SERVER_PASSWORD,
                    database=settings.SQL_SERVER_DB,
                    timeout=timeout,
                )
            except Exception as e:
                last_error = e
                if attempt < retries:
                    self._increment_retry_metric("connect_retries", context)
                    delay = base_delay_seconds * attempt
                    print(
                        f"⚠️ Conexión SQL falló ({context}) intento {attempt}/{retries}: {e}. "
                        f"Reintentando en {delay}s... "
                        f"[metrics connect_retries={self.sql_retry_stats.get('connect_retries', 0)}]"
                    )
                    time.sleep(delay)

        raise last_error

    def _execute_with_retry(
        self,
        cursor,
        query: str,
        params: tuple = (),
        retries: int = 2,
        base_delay_seconds: int = 1,
        context: str = "sql_query",
    ):
        """Ejecuta una consulta SQL con reintentos para queries propensas a timeout."""
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                cursor.execute(query, params)
                return
            except Exception as e:
                last_error = e
                if attempt < retries:
                    self._increment_retry_metric("query_retries", context)
                    delay = base_delay_seconds * attempt
                    print(
                        f"⚠️ Consulta SQL falló ({context}) intento {attempt}/{retries}: {e}. "
                        f"Reintentando en {delay}s... "
                        f"[metrics query_retries={self.sql_retry_stats.get('query_retries', 0)}]"
                    )
                    time.sleep(delay)

        raise last_error

    def _resolve_user_identity(self, user_id: str) -> Dict[str, Optional[str]]:
        """Resuelve username/login/resource para aceptar user_id como Username o GUID."""
        raw_user = (user_id or "").strip()
        identity: Dict[str, Optional[str]] = {
            "input": raw_user,
            "username": raw_user or None,
            "login_id": None,
            "resource_id": None,
            "full_name": None,
            "display_name": None,
        }
        if not raw_user:
            return identity

        conn = None
        try:
            conn = self._connect_sql_with_retry(
                timeout=max(1, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
                retries=max(1, settings.DB_INGEST_CONNECT_RETRIES),
                base_delay_seconds=1,
                context="resolve_user_identity",
            )
            cursor = conn.cursor(as_dict=True)
            self._execute_with_retry(
                cursor=cursor,
                query="""
                    SELECT TOP 1
                        sl.IDLogin,
                        sl.Username,
                        sl.FullName,
                        sr.ResourceId,
                        sr.DisplayName
                    FROM dbo.SysLogin sl
                    LEFT JOIN dbo.SysResources sr
                        ON sr.ActiveIDLogin2Resource = sl.ActiveIDLogin2Resource
                    WHERE sl.Username = %s
                       OR sl.IDLogin = TRY_CONVERT(uniqueidentifier, %s)
                       OR sr.ResourceId = TRY_CONVERT(uniqueidentifier, %s)
                    ORDER BY CASE WHEN sl.Username = %s THEN 0 ELSE 1 END,
                             sl.Username
                """,
                params=(raw_user, raw_user, raw_user, raw_user),
                retries=1,
                base_delay_seconds=1,
                context="resolve_user_identity_query",
            )
            print(f"🔍 Resolviendo identidad de usuario '{raw_user}'...")
            row = cursor.fetchone()
            conn.close()

            if row:
                identity["username"] = (row.get("Username") or raw_user).strip() if row.get("Username") else raw_user
                identity["login_id"] = str(row.get("IDLogin") or "").strip() or None
                identity["resource_id"] = str(row.get("ResourceId") or "").strip() or None
                identity["full_name"] = (row.get("FullName") or "").strip() or None
                identity["display_name"] = (row.get("DisplayName") or "").strip() or None

            return identity
        except Exception as e:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            print(f"⚠️ No se pudo resolver identidad de usuario '{raw_user}': {e}")
            return identity

    def _get_user_resource_id(self, username: str) -> Optional[str]:
        """Obtiene el ResourceId de un usuario por su username."""
        if not username:
            return None
        
        conn = None
        try:
            conn = self._connect_sql_with_retry(
                timeout=5,
                retries=2,
                base_delay_seconds=1,
                context="get_user_resource",
            )
            cursor = conn.cursor(as_dict=True)
            cursor.execute(
                """
                SELECT TOP 1 sr.ResourceId
                FROM dbo.SysLogin sl
                INNER JOIN dbo.SysResources sr 
                    ON sr.ActiveIDLogin2Resource = sl.ActiveIDLogin2Resource
                WHERE sl.Username = %s
                """,
                (username,)
            )
            row = cursor.fetchone()
            conn.close()
            return str(row.get("ResourceId")) if row else None
        except Exception as e:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            print(f"⚠️ Error obteniendo ResourceId para usuario '{username}': {e}")
            return None

    # ============================================================
    # 1. OBTENER CONTEXTO DEL USUARIO
    # ============================================================
    
    def obtener_contexto_usuario(self, user_id: str) -> Optional[ContextoUsuario]:
        """
        Obtiene todo el contexto de un usuario para personalizar respuestas.
        """
        return self._obtener_contexto_usuario_fallback(user_id)

    def _obtener_contexto_usuario_fallback(self, user_id: str) -> Optional[ContextoUsuario]:
        """
        Fallback para esquemas donde no existen las tablas dbo.Recursos* y dbo.Canales.

        Mapeo rápido aplicado (basado en tablas reales del sistema):
        - RecursoHumano -> dbo.SysPerson
        - Roles -> dbo._SysRole_SysResource + dbo.SysRole
        - Canales del usuario -> dbo.SysWorkRoomResource + dbo.SysWorkRoom
        - Actividad de chat -> dbo.SysChat + dbo.SysChat2SysWorkRoom + dbo.SysChat2SysResource
        - Entidades relacionadas al chat -> dbo.SysChat2Record
        """
        try:
            identity = self._resolve_user_identity(user_id)
            username = (identity.get("username") or user_id or "").strip()
            resolved_resource_id = (identity.get("resource_id") or "").strip()
            resolved_login_id = (identity.get("login_id") or "").strip()

            conn = self._connect_sql_with_retry(
                timeout=10,
                retries=3,
                base_delay_seconds=1,
                context="contexto_usuario_fallback",
            )
            cursor = conn.cursor(as_dict=True)

            cursor.execute(
                """
                SELECT TOP 1
                    IDResource,
                    IDUser,
                    accountname,
                    firstname,
                    lastname,
                    email,
                    departament,
                    title
                FROM dbo.SysPerson
                WHERE IDResource = TRY_CONVERT(uniqueidentifier, %s)
                   OR IDUser = TRY_CONVERT(uniqueidentifier, %s)
                   OR UPPER(accountname) = UPPER(%s)
                   OR organization_no = %s
                   OR reference = %s
                """,
                (resolved_resource_id or user_id, resolved_login_id or user_id, username, user_id, user_id),
            )
            usuario_data = cursor.fetchone()
            if not usuario_data:
                conn.close()
                return None

            nombre = (
                usuario_data.get("accountname")
                or identity.get("full_name")
                or identity.get("display_name")
                or f"{(usuario_data.get('firstname') or '').strip()} {(usuario_data.get('lastname') or '').strip()}".strip()
                or str(usuario_data.get("IDResource") or user_id)
            )

            resource_guid = str(usuario_data.get("IDResource") or resolved_resource_id or user_id)
            user_guid = str(resolved_login_id or usuario_data.get("IDUser") or "")

            # Resolver rol desde tablas del sistema, con fallback al título del contacto.
            rol = None
            try:
                cursor.execute(
                    """
                    SELECT TOP 1 sr.Code
                    FROM dbo._SysRole_SysResource srs
                    INNER JOIN dbo.SysRole sr ON sr.IDActivityRole = srs.IDRole
                    WHERE srs.IDResource = TRY_CONVERT(uniqueidentifier, %s)
                    ORDER BY sr.ShareChat DESC, sr.ViewScheduler DESC, sr.Code
                    """,
                    (resource_guid,),
                )
                rol_row = cursor.fetchone()
                if rol_row and rol_row.get("Code"):
                    rol = str(rol_row.get("Code")).strip()
            except Exception:
                rol = None

            if not rol:
                rol = (usuario_data.get("title") or "operario").strip() if usuario_data.get("title") else "operario"

            especialidades = [rol] if rol else []

            usuario = RecursoHumano(
                id=resource_guid,
                nombre=nombre,
                email=(usuario_data.get("email") or ""),
                rol=rol,
                departamento=usuario_data.get("departament"),
                especialidades=especialidades,
                canales=[],
            )

            # Obtener canales del usuario con miembros en una sola consulta
            cursor.execute(
                """
                WITH user_rooms AS (
                    SELECT DISTINCT 
                        wr.IDWorkRoom,
                        wr.Name,
                        wr.Description,
                        wr.Kind
                    FROM dbo.SysWorkRoomResource wrr
                    INNER JOIN dbo.SysWorkRoom wr 
                        ON wr.IDWorkRoom = wrr.IDWorkRoom
                    WHERE wrr.IDResource = TRY_CONVERT(uniqueidentifier, %s)
                       OR wrr.IDLogin = TRY_CONVERT(uniqueidentifier, %s)
                       OR EXISTS (
                           SELECT 1 
                           FROM dbo.SysLogin slu 
                           WHERE slu.IDLogin = wrr.IDLogin 
                             AND UPPER(slu.Username) = UPPER(%s)
                       )
                ),
                room_members AS (
                    SELECT 
                        wrr.IDWorkRoom,
                        STRING_AGG(
                            COALESCE(
                                CONVERT(varchar(36), wrr.IDResource), 
                                CONVERT(varchar(36), wrr.IDLogin)
                            ), 
                            ','
                        ) AS Members
                    FROM dbo.SysWorkRoomResource wrr
                    INNER JOIN user_rooms ur ON ur.IDWorkRoom = wrr.IDWorkRoom
                    GROUP BY wrr.IDWorkRoom
                )
                SELECT 
                    ur.IDWorkRoom,
                    ur.Name,
                    ur.Description,
                    ur.Kind,
                    rm.Members
                FROM user_rooms ur
                LEFT JOIN room_members rm ON rm.IDWorkRoom = ur.IDWorkRoom
                ORDER BY ur.Name
                """,
                (resource_guid, user_guid, username),
            )
            canales_rows = cursor.fetchall() or []

            canales = []
            for row in canales_rows:
                canal_id = str(row.get("IDWorkRoom")) if row.get("IDWorkRoom") else None
                if not canal_id:
                    continue
                canal_nombre = row.get("Name") or f"Canal {canal_id[:8]}"
                canal_descripcion = row.get("Description") or "Canal de trabajo"
                
                # Parsear miembros de la cadena concatenada
                miembros_str = row.get("Members") or ""
                miembros = [m.strip() for m in miembros_str.split(',') if m.strip()] if miembros_str else []

                canal = Canal(
                    id=canal_id,
                    nombre=str(canal_nombre).strip(),
                    descripcion=str(canal_descripcion).strip(),
                    tipo=f"workroom_{row.get('Kind')}",
                    recursos_humanos=miembros,
                    recursos_materiales=[],
                )
                canales.append(canal)
                usuario.canales.append(canal_id)

            # Obtener actividades recientes del chat
            try:
                self._execute_with_retry(
                    cursor=cursor,
                    query="""
                        SELECT TOP 20
                            c.IDChat2,
                            c.Stamp,
                            c.RawMessage,
                            COALESCE(c.IDWorkRoom, c2w.IDWorkRoom) as IDChannel,
                            wr.Name as ChannelName,
                            wr.Description as ChannelDescription,
                            c2r.RecordCode,
                            c2r.RecordShortName
                        FROM dbo.SysChat2SysResource c2rsc
                        INNER JOIN dbo.SysChat c ON c.IDChat2 = c2rsc.IDChat
                        LEFT JOIN dbo.SysChat2SysWorkRoom c2w ON c2w.IDChat2 = c.IDChat2
                        LEFT JOIN dbo.SysWorkRoom wr ON wr.IDWorkRoom = COALESCE(c.IDWorkRoom, c2w.IDWorkRoom)
                        OUTER APPLY (
                            SELECT TOP 1 r.RecordCode, r.RecordShortName
                            FROM dbo.SysChat2Record r
                            WHERE r.IDChat = c.IDChat2
                            ORDER BY r.Stamp DESC
                        ) c2r
                        WHERE c2rsc.IDResource = TRY_CONVERT(uniqueidentifier, %s)
                           OR c2rsc.IDLogin = TRY_CONVERT(uniqueidentifier, %s)
                           OR EXISTS (
                               SELECT 1
                               FROM dbo.SysLogin slu
                               WHERE slu.IDLogin = c2rsc.IDLogin
                                 AND UPPER(slu.Username) = UPPER(%s)
                           )
                        ORDER BY c.Stamp DESC
                    """,
                    params=(resource_guid, user_guid, username),
                    retries=2,
                    base_delay_seconds=1,
                    context="fallback_chat_actividad",
                )
                actividades_rows = cursor.fetchall() or []
            except Exception as e:
                print(f"⚠️ Fallback chat-actividad no disponible: {e}")
                actividades_rows = []

            actividades = []
            for a in actividades_rows:
                canal_id = str(a.get("IDChannel")) if a.get("IDChannel") else "canal_general"
                mensaje = (a.get("RawMessage") or "").strip()
                record_code = (a.get("RecordCode") or "").strip()
                record_short = (a.get("RecordShortName") or "").strip()
                descripcion = mensaje
                if record_code or record_short:
                    descripcion = f"{descripcion} | Registro relacionado: {record_code} {record_short}".strip()
                timestamp = a.get("Stamp") or datetime.now()
                actividades.append(
                    Actividad(
                        id=str(a.get("IDChat2")),
                        recurso_humano_id=str(usuario.id),
                        canal_id=canal_id,
                        tipo="chat",
                        descripcion=descripcion or "Sin descripción",
                        timestamp=timestamp,
                        metadatos={
                            "channel_name": a.get("ChannelName"),
                            "channel_description": a.get("ChannelDescription"),
                            "record_code": record_code,
                        },
                    )
                )

            # Obtener recursos disponibles
            try:
                self._execute_with_retry(
                    cursor=cursor,
                    query="""
                        SELECT TOP 30
                            c2r.RecordCode,
                            c2r.RecordShortName,
                            c2w.IDWorkRoom
                        FROM dbo.SysChat2SysResource c2rsc
                        INNER JOIN dbo.SysChat c ON c.IDChat2 = c2rsc.IDChat
                        LEFT JOIN dbo.SysChat2SysWorkRoom c2w ON c2w.IDChat2 = c.IDChat2
                        INNER JOIN dbo.SysChat2Record c2r ON c2r.IDChat = c.IDChat2
                        WHERE c2rsc.IDResource = TRY_CONVERT(uniqueidentifier, %s)
                           OR c2rsc.IDLogin = TRY_CONVERT(uniqueidentifier, %s)
                           OR EXISTS (
                               SELECT 1
                               FROM dbo.SysLogin slu
                               WHERE slu.IDLogin = c2rsc.IDLogin
                                 AND UPPER(slu.Username) = UPPER(%s)
                           )
                        ORDER BY c2r.Stamp DESC
                    """,
                    params=(resource_guid, user_guid, username),
                    retries=2,
                    base_delay_seconds=1,
                    context="fallback_chat_recursos",
                )
                materiales_rows = cursor.fetchall() or []
            except Exception as e:
                print(f"⚠️ Fallback recursos de chat no disponibles: {e}")
                materiales_rows = []
            
            recursos_disponibles = []
            for m in materiales_rows:
                record_code = (m.get("RecordCode") or "").strip()
                if not record_code:
                    continue
                canal_id = str(m.get("IDWorkRoom")) if m.get("IDWorkRoom") else "canal_general"
                recursos_disponibles.append(
                    RecursoMaterial(
                        id=record_code,
                        nombre=(m.get("RecordShortName") or record_code),
                        tipo="chat_record",
                        canal_id=canal_id,
                        estado="disponible",
                        especificaciones={"record_code": record_code},
                    )
                )

            conn.close()

            permisos = self._obtener_permisos_por_rol(usuario.rol)
            return ContextoUsuario(
                usuario=usuario,
                canales_acceso=canales,
                actividades_recientes=actividades,
                recursos_disponibles=recursos_disponibles,
                permisos=permisos,
            )
        except Exception as e:
            print(f"❌ Error en fallback de contexto de usuario: {e}")
            return None
    
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
        return permisos_base + permisos_rol.get(rol, [])

    def obtener_recurso_id_por_nombre(self, nombre: str) -> Optional[str]:
        """Busca el ID de recurso por nombre visible en SysResources."""
        nombre = (nombre or "").strip()
        if not nombre:
            return None

        try:
            conn = self._connect_sql_with_retry(
                timeout=10,
                retries=2,
                base_delay_seconds=1,
                context="resource_by_name",
            )
            cursor = conn.cursor(as_dict=True)
            self._execute_with_retry(
                cursor=cursor,
                query="""
                    SELECT TOP 1 ResourceId, DisplayName
                    FROM dbo.SysResources
                    WHERE UPPER(DisplayName) LIKE UPPER(%s)
                    ORDER BY CASE WHEN UPPER(DisplayName) = UPPER(%s) THEN 0 ELSE 1 END, DisplayName
                """,
                params=(f"%{nombre}%", nombre),
                retries=2,
                base_delay_seconds=1,
                context="resource_by_name_query",
            )
            row = cursor.fetchone()
            conn.close()
            if not row:
                return None
            return str(row.get("ResourceId") or row.get("IDResource") or "").strip() or None
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
        """
        Obtiene mensajes recientes de chat desde BD con validación de membresía.
        Versión ultra-rápida usando CTE para filtrar canales del usuario.
        """
        if not user_id:
            return []

        identity = self._resolve_user_identity(user_id)
        username = (identity.get("username") or user_id or "").strip()
        
        if not username:
            print("⚠️ No se pudo resolver el username del usuario")
            return []

        safe_limit = max(1, min(limit, 30))
        safe_offset = max(0, offset)
        fetch_limit = safe_limit + safe_offset

        # Query ultra-rápida con CTE para filtrar canales del usuario
        query = """
            WITH user_channels AS (
                -- Obtener todos los canales donde el usuario es miembro
                SELECT DISTINCT wrr.IDWorkRoom
                FROM dbo.SysWorkRoomResource wrr
                LEFT JOIN dbo.SysLogin sl ON sl.IDLogin = wrr.IDLogin
                WHERE wrr.IDResource = (SELECT TOP 1 ResourceId 
                                        FROM dbo.SysResources 
                                        WHERE ActiveIDLogin2Resource = 
                                            (SELECT TOP 1 ActiveIDLogin2Resource 
                                             FROM dbo.SysLogin 
                                             WHERE Username = %s))
                   OR sl.Username = %s
            )
            SELECT TOP %s
                c.IDChat2,
                c.Stamp,
                c.RawMessage,
                wr.IDWorkRoom AS IDChannel,
                wr.Name AS ChannelName,
                sr.ResourceId AS SenderResourceId,
                sr.DisplayName AS SenderDisplayName,
                sl.FullName AS SenderFullName,
                sl.Username AS SenderUsername
            FROM dbo.SysChat c
            INNER JOIN dbo.SysChat2SysWorkRoom c2w 
                ON c.IDChat2 = c2w.IDChat2
            INNER JOIN dbo.SysWorkRoom wr 
                ON wr.IDWorkRoom = c2w.IDWorkRoom
            INNER JOIN dbo.SysWorkRoomResource wrr 
                ON wrr.IDWorkRoom = wr.IDWorkRoom
            INNER JOIN dbo.SysResources sr 
                ON sr.ResourceId = wrr.IDResource
            INNER JOIN dbo.SysLogin sl 
                ON sl.ActiveIDLogin2Resource = sr.ActiveIDLogin2Resource
            WHERE wr.IDWorkRoom IN (SELECT IDWorkRoom FROM user_channels)
        """
        
        params: List[Any] = [username, username, fetch_limit]
        
        # Filtrar por canal específico si se proporciona
        if canal_id:
            query += " AND wr.IDWorkRoom = %s"
            params.append(canal_id)
        
        # Filtrar por remitente específico si se proporciona
        if sender_resource_id:
            query += " AND sr.ResourceId = %s"
            params.append(sender_resource_id)
        
        # Agrupar para eliminar duplicados (por si un recurso tiene múltiples logins)
        query += """
            GROUP BY 
                c.IDChat2,
                c.Stamp,
                c.RawMessage,
                wr.IDWorkRoom,
                wr.Name,
                sr.ResourceId,
                sr.DisplayName,
                sl.FullName,
                sl.Username
            ORDER BY 
                c.Stamp DESC
        """
        
        conn = None
        try:
            conn = self._connect_sql_with_retry(
                timeout=max(1, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
                retries=max(1, settings.DB_INGEST_CONNECT_RETRIES),
                base_delay_seconds=1,
                context="chat_messages_ultra",
            )
            cursor = conn.cursor(as_dict=True)
            
            self._execute_with_retry(
                cursor=cursor,
                query=query,
                params=tuple(params),
                retries=2,
                base_delay_seconds=1,
                context="chat_messages_ultra_query",
            )
            rows = cursor.fetchall() or []
            conn.close()

            messages: List[Dict[str, Any]] = []
            for row in rows:
                raw_message = (row.get("RawMessage") or "").strip()
                if not raw_message:
                    continue

                messages.append({
                    "chat_id": str(row.get("IDChat2") or ""),
                    "timestamp": row.get("Stamp"),
                    "message": raw_message,
                    "channel_id": str(row.get("IDChannel") or canal_id),
                    "channel_name": (row.get("ChannelName") or "Canal sin nombre").strip(),
                    "sender_resource_id": str(row.get("SenderResourceId") or "") or None,
                    "sender_display_name": (row.get("SenderDisplayName") or "").strip() or None,
                    "sender_full_name": (row.get("SenderFullName") or "").strip() or None,
                    "sender_username": (row.get("SenderUsername") or "").strip() or None,
                })

            # Aplicar offset y limit
            if safe_offset > 0:
                messages = messages[safe_offset:]
            if len(messages) > safe_limit:
                messages = messages[:safe_limit]

            print(f"✅ Mensajes obtenidos: {len(messages)} para usuario {username}")
            return messages
            
        except Exception as e:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            print(f"⚠️ Error obteniendo mensajes de chat desde BD: {e}")
            return []

    def obtener_usuarios_recurso_del_canal(
        self,
        user_id: str,
        canal_id: Optional[str],
        limit: int = 80,
    ) -> List[Dict[str, Any]]:
        """Obtiene usuarios recurso del canal de forma liviana y con validación de acceso."""
        identity = self._resolve_user_identity(user_id)
        username = (identity.get("username") or user_id or "").strip()
        resolved_resource_id = (identity.get("resource_id") or user_id or "").strip()
        resolved_login_id = (identity.get("login_id") or user_id or "").strip()

        if not user_id or not (canal_id or "").strip():
            return []

        safe_limit = max(1, min(limit, 120))
        try:
            conn = self._connect_sql_with_retry(
                timeout=max(1, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
                retries=max(1, settings.DB_INGEST_CONNECT_RETRIES),
                base_delay_seconds=1,
                context="channel_members",
            )
            cursor = conn.cursor(as_dict=True)

            # Validar que el usuario solicitante pertenezca al canal (por recurso o login).
            self._execute_with_retry(
                cursor=cursor,
                query="""
                    SELECT TOP 1 1 AS allowed
                    FROM dbo.SysWorkRoomResource req
                    WHERE req.IDWorkRoom = TRY_CONVERT(uniqueidentifier, %s)
                      AND (
                          req.IDResource = TRY_CONVERT(uniqueidentifier, %s)
                          OR req.IDLogin = TRY_CONVERT(uniqueidentifier, %s)
                          OR EXISTS (
                              SELECT 1
                              FROM dbo.SysLogin slu
                              WHERE slu.IDLogin = req.IDLogin
                                AND UPPER(slu.Username) = UPPER(%s)
                          )
                      )
                """,
                params=(canal_id, resolved_resource_id, resolved_login_id, username),
                retries=1,
                base_delay_seconds=1,
                context="channel_members_acl",
            )
            acl_row = cursor.fetchone()
            if not acl_row:
                conn.close()
                return []

            self._execute_with_retry(
                cursor=cursor,
                query=f"""
                    SELECT TOP {safe_limit}
                        wr.IDWorkRoom,
                        wr.Name AS ChannelName,
                        COALESCE(sr.ResourceId, wrr.IDResource) AS ResourceId,
                        COALESCE(sr.DisplayName, sl.FullName, sl.Username, 'Sin nombre') AS DisplayName,
                        sl.Username,
                        sl.FullName,
                        wrr.IDLogin
                    FROM dbo.SysWorkRoomResource wrr
                    INNER JOIN dbo.SysWorkRoom wr
                        ON wr.IDWorkRoom = wrr.IDWorkRoom
                    INNER JOIN dbo.SysResources sr
                        ON sr.ResourceId = wrr.IDResource
                    INNER JOIN dbo.SysLogin sl
                        ON sl.ActiveIDLogin2Resource = sr.ActiveIDLogin2Resource
                    WHERE wrr.IDWorkRoom = TRY_CONVERT(uniqueidentifier, %s)
                    ORDER BY COALESCE(sr.DisplayName, sl.FullName, sl.Username, 'Sin nombre')
                """,
                params=(canal_id,),
                retries=1,
                base_delay_seconds=1,
                context="channel_members_query",
            )

            rows = cursor.fetchall() or []
            conn.close()

            members: List[Dict[str, Any]] = []
            for row in rows:
                members.append(
                    {
                        "channel_id": str(row.get("IDWorkRoom") or canal_id),
                        "channel_name": (row.get("ChannelName") or "Canal sin nombre").strip(),
                        "resource_id": str(row.get("ResourceId") or "") or None,
                        "display_name": (row.get("DisplayName") or "Sin nombre").strip(),
                        "username": (row.get("Username") or "").strip() or None,
                        "full_name": (row.get("FullName") or "").strip() or None,
                        "login_id": str(row.get("IDLogin") or "") or None,
                    }
                )

            return members
        except Exception as e:
            print(f"⚠️ Error obteniendo usuarios recurso del canal: {e}")
            return []

    def obtener_contexto_chat_desde_bd(self, user_id: str, canal_id: Optional[str] = None, limit: int = 8) -> str:
        """
        Obtiene contexto reciente del chat directamente desde la base de datos.
        Prioriza SysChat + SysChat2SysResource + SysChat2SysWorkRoom + SysWorkRoom.
        """
        if not user_id:
            return ""

        safe_limit = max(1, min(limit, 30))
        rows = self.obtener_mensajes_chat_desde_bd(
            user_id=user_id,
            canal_id=canal_id,
            limit=safe_limit,
            offset=0,
            sender_resource_id=None,
        )

        if not rows:
            return "No hay historial de chat reciente en la base de datos para este usuario."

        lines = []
        for row in rows:
            ts = row.get("timestamp")
            ts_text = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else "sin_fecha"
            channel_name = row.get("channel_name") or "Canal sin nombre"
            msg = row.get("message") or ""
            if len(msg) > 180:
                msg = msg[:180] + "..."
            sender_name = row.get("sender_display_name") or row.get("sender_username") or ""
            sender_text = f" [{sender_name}]" if sender_name else ""
            lines.append(f"[{ts_text}] ({channel_name}){sender_text} {msg}")

        if not lines:
            return "No hay mensajes de chat útiles para contexto en base de datos."

        return "\n".join(lines)
    
    # ============================================================
    # 3. GENERAR CONTEXTO PARA EL AGENTE (en texto)
    # ============================================================
    
    def generar_contexto_agente(self, user_id: str) -> str:
        """
        Genera un texto de contexto para inyectar en el System Prompt del agente.
        """
        contexto = self.obtener_contexto_usuario(user_id)
        if not contexto:
            return "No se pudo obtener el contexto del usuario."
        
        # Construir el contexto en texto plano
        texto = f"""
        === CONTEXTO DEL USUARIO ===
        Usuario: {contexto.usuario.nombre}
        Rol: {contexto.usuario.rol}
        Departamento: {contexto.usuario.departamento or 'No especificado'}
        Especialidades: {', '.join(contexto.usuario.especialidades) if contexto.usuario.especialidades else 'No especificadas'}
        
        === CANALES A LOS QUE TIENE ACCESO ===
        """
        
        for canal in contexto.canales_acceso:
            texto += f"""
        📋 Canal: {canal.nombre}
           Tipo: {canal.tipo}
           Descripción: {canal.descripcion}
           Colaboradores en este canal: {len(canal.recursos_humanos)} personas
           Recursos materiales disponibles: {len([r for r in contexto.recursos_disponibles if r.canal_id == canal.id])}
            """
        
        texto += "\n=== ACTIVIDADES RECIENTES (Últimos 7 días) ===\n"
        for act in contexto.actividades_recientes[:10]:
            texto += f"  • {act.tipo}: {act.descripcion[:100]}... ({act.timestamp.strftime('%d/%m/%Y')})\n"
        
        texto += f"\n=== RECURSOS DISPONIBLES ===\n"
        for recurso in contexto.recursos_disponibles[:10]:
            texto += f"  • {recurso.nombre} ({recurso.tipo}) - Estado: {recurso.estado}\n"
        
        texto += f"\n=== PERMISOS DEL USUARIO ===\n"
        texto += f"  {', '.join(contexto.permisos)}\n"
        
        texto += """
        === REGLAS DE RESPUESTA SEGÚN ROL ===
        """
        
        if contexto.usuario.rol == "operario":
            texto += """
            - El operario necesita respuestas prácticas y directas.
            - Enfócate en acciones concretas que pueda ejecutar.
            - Si hay un problema, sugiere pasos claros de solución.
            """
        elif contexto.usuario.rol == "supervisor":
            texto += """
            - El supervisor necesita una visión general del estado.
            - Proporciona estadísticas y resúmenes de actividad.
            - Sugiere asignaciones de tareas si es pertinente.
            """
        elif contexto.usuario.rol == "ingeniero":
            texto += """
            - El ingeniero necesita datos técnicos detallados.
            - Proporciona parámetros, diagnósticos y análisis.
            - Puedes ser más técnico en las respuestas.
            """
        elif contexto.usuario.rol == "gerente":
            texto += """
            - El gerente necesita visión estratégica y reportes.
            - Enfócate en KPIs, eficiencia y productividad.
            - Evita tecnicismos excesivos.
            """
        
        return texto
    
    # ============================================================
    # 4. APRENDER DE LAS ACTIVIDADES (RAG)
    # ============================================================
    
    def aprender_actividad(self, actividad: Actividad) -> bool:
        """
        Aprende de una actividad realizada por un usuario.
        Indexa el conocimiento en Qdrant para futuras consultas.
        """
        try:
            metadatos = actividad.metadatos or {}
            source_table = metadatos.get("source_table")

            # Para ingestas del sistema no tiene sentido consultar el esquema de usuarios humano.
            # Usamos los datos del propio evento para evitar ruido y fallos repetidos.
            if actividad.recurso_humano_id == "sistema" or source_table in {"SysRole", "SysResources", "SysChat"}:
                usuario_nombre = metadatos.get("display_name") or metadatos.get("role_code") or actividad.recurso_humano_id
                usuario_rol = source_table or "sistema"
                usuario_departamento = "No especificado"
                usuario_especialidades = source_table or "No especificadas"
                usuario_permisos = "consultar_informacion"
                contexto = None
            else:
                # Obtener contexto del usuario que realizó la actividad
                contexto = self.obtener_contexto_usuario(actividad.recurso_humano_id)
                usuario_nombre = actividad.recurso_humano_id
                usuario_rol = "desconocido"
                usuario_departamento = "No especificado"
                usuario_especialidades = "No especificadas"
                usuario_permisos = "consultar_informacion"

            
            if contexto:
                usuario_nombre = contexto.usuario.nombre
                usuario_rol = contexto.usuario.rol
                usuario_departamento = contexto.usuario.departamento or "No especificado"
                usuario_especialidades = ", ".join(contexto.usuario.especialidades) if contexto.usuario.especialidades else "No especificadas"
                usuario_permisos = ", ".join(contexto.permisos)
            
            # Construir el texto de aprendizaje
            texto_aprendizaje = f"""
            Actividad realizada por {usuario_nombre} ({usuario_rol}) en el canal {actividad.canal_id}:
            Tipo: {actividad.tipo}
            Descripción: {actividad.descripcion}
            Fecha: {actividad.timestamp}
            
            Contexto del usuario:
            - Departamento: {usuario_departamento}
            - Especialidades: {usuario_especialidades}
            - Permisos: {usuario_permisos}
            """
            
            # Generar embedding con protección (evita congelar la ingesta si Ollama cae).
            vector = self._embed_query_safe(texto_aprendizaje, context="aprender_actividad")
            if vector is None:
                return False
            
            # ID basado en hash para evitar duplicados
            point_id = str(uuid.UUID(hashlib.md5(texto_aprendizaje.encode()).hexdigest()))
            
            point = PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "page_content": texto_aprendizaje,
                    "tipo": actividad.tipo,
                    "canal_id": actividad.canal_id,
                    "recurso_humano_id": actividad.recurso_humano_id,
                    "rol_usuario": usuario_rol,
                    "timestamp": actividad.timestamp.isoformat(),
                    "session_id": (actividad.metadatos or {}).get("session_id"),
                    "herramientas_usadas": (actividad.metadatos or {}).get("herramientas_usadas", []),
                    "longitud_consulta": (actividad.metadatos or {}).get("longitud_consulta"),
                    "longitud_respuesta": (actividad.metadatos or {}).get("longitud_respuesta"),
                    "metadatos": actividad.metadatos or {},
                    "source": "actividad_aprendida"
                }
            )
            
            self.qdrant.upsert(
                collection_name=self.collection,
                points=[point]
            )
            return True
            
        except Exception as e:
            print(f"❌ Error aprendiendo actividad: {e}")
            return False
    
    def aprender_canal(self, canal: Canal) -> bool:
        """
        Aprende la estructura y dinámica de un canal.
        """
        try:
            texto_canal = f"""
            Canal de trabajo: {canal.nombre}
            Tipo: {canal.tipo}
            Descripción: {canal.descripcion}
            
            Recursos humanos asignados: {len(canal.recursos_humanos)} personas
            Recursos materiales: {len(canal.recursos_materiales)} elementos
            Proyectos activos: {', '.join(canal.proyectos_activos) if canal.proyectos_activos else 'Ninguno'}
            """
            
            vector = self._embed_query_safe(texto_canal, context="aprender_canal")
            if vector is None:
                return False
            
            point_id = str(uuid.UUID(hashlib.md5(canal.id.encode()).hexdigest()))
            
            point = PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "page_content": texto_canal,
                    "canal_id": canal.id,
                    "tipo": canal.tipo,
                    "source": "estructura_canal"
                }
            )
            
            self.qdrant.upsert(
                collection_name=self.collection,
                points=[point]
            )
            return True
            
        except Exception as e:
            print(f"❌ Error aprendiendo canal: {e}")
            return False
    
    # ============================================================
    # 5. CONSULTAR CONOCIMIENTO APRENDIDO (para el agente)
    # ============================================================
    
    def _search_aprendizaje(self, query_vector, query_filter: Optional[dict], limit: int):
        """
        Busca en Qdrant utilizando un filtro opcional.
        """
        try:
            collections = [c.name for c in self.qdrant.get_collections().collections]
            if self.collection not in collections:
                return []

            # Compatibilidad con versiones nuevas de qdrant-client.
            try:
                search_result = self.qdrant.query_points(
                    collection_name=self.collection,
                    query=query_vector,
                    limit=limit,
                    query_filter=query_filter,
                )
                return search_result.points if hasattr(search_result, "points") else (search_result or [])
            except AttributeError:
                resultados = self.qdrant.search(
                    collection_name=self.collection,
                    query_vector=query_vector,
                    limit=limit,
                    query_filter=query_filter,
                )
                return resultados or []
        except Exception as e:
            print(f"❌ Error en búsqueda de aprendizaje: {e}")
            return []

    def _extract_hit_id(self, hit) -> Optional[str]:
        if hasattr(hit, 'id') and getattr(hit, 'id') is not None:
            return getattr(hit, 'id')
        if isinstance(hit, dict):
            return hit.get('id') or hit.get('payload', {}).get('id')
        if hasattr(hit, 'payload') and hit.payload:
            return hit.payload.get('id')
        return None

    def _format_aprendizaje_results(self, resultados) -> str:
        if not resultados:
            return "No hay conocimiento previo relacionado con esta consulta."

        texto_resultado = "📚 CONOCIMIENTO APRENDIDO RELACIONADO:\n\n"
        seen_ids = set()
        count = 0
        for hit in resultados:
            hit_id = self._extract_hit_id(hit)
            page_content = None
            if hasattr(hit, 'payload') and hit.payload:
                page_content = hit.payload.get('page_content', '')
            elif isinstance(hit, dict):
                page_content = hit.get('payload', {}).get('page_content', '')

            if not page_content:
                continue

            unique_key = hit_id or page_content[:120]
            if unique_key in seen_ids:
                continue
            seen_ids.add(unique_key)

            count += 1
            texto_resultado += f"{count}. {page_content[:300]}...\n\n"
            if count >= 5:
                break

        return texto_resultado if count > 0 else "No hay conocimiento previo relacionado con esta consulta."

    def consultar_aprendizaje(self, query: str, canal_id: Optional[str] = None, limit: int = 3) -> str:
        """
        Consulta el conocimiento aprendido por el sistema.
        Puede filtrar por canal para dar contexto específico, pero siempre incluye resultados generales.
        """
        try:
            query_vector = self._embed_query_safe(query, context="consultar_aprendizaje")
            if query_vector is None:
                return "No hay conocimiento previo relacionado con esta consulta."

            resultados = []
            canal_results = []
            general_results = []

            # Buscar por canal si se especifica
            if canal_id:
                canal_filter = {"must": [{"key": "canal_id", "match": {"value": canal_id}}]}
                canal_results = self._search_aprendizaje(query_vector, canal_filter, limit)

            # Buscar conocimiento general sin filtro de canal
            general_results = self._search_aprendizaje(query_vector, None, limit)

            if canal_results:
                resultados.extend(canal_results)

            # Añadir resultados generales adicionales que no estén duplicados
            canal_ids = {self._extract_hit_id(hit) for hit in canal_results if hit and self._extract_hit_id(hit) is not None}
            for hit in general_results:
                hit_id = self._extract_hit_id(hit)
                if hit_id is not None and hit_id in canal_ids:
                    continue
                resultados.append(hit)
                if len(resultados) >= limit:
                    break

            return self._format_aprendizaje_results(resultados[:limit])

        except Exception as e:
            return f"Error consultando aprendizaje: {str(e)}"
    
    # ============================================================
    # 6. SUGERIR COLABORADORES (basado en actividades pasadas)
    # ============================================================
    
    def sugerir_colaboradores(self, canal_id: str, tipo_actividad: str) -> List[Dict]:
        """
        Sugiere recursos humanos que han realizado actividades similares en el pasado.
        """
        try:
            # Buscar actividades similares en Qdrant
            query = f"Actividad tipo {tipo_actividad} en canal {canal_id}"
            query_vector = self.embeddings.embed_query(query)
            
            resultados = self._search_aprendizaje(query_vector, None, 10)
            
            colaboradores = {}
            for hit in resultados:
                payload = hit.payload
                if payload.get('source') == 'actividad_aprendida':
                    usuario_id = payload.get('recurso_humano_id')
                    rol = payload.get('rol_usuario')
                    if usuario_id and usuario_id not in colaboradores:
                        colaboradores[usuario_id] = {
                            'id': usuario_id,
                            'rol': rol,
                            'actividades_similares': 0
                        }
                    if usuario_id in colaboradores:
                        colaboradores[usuario_id]['actividades_similares'] += 1
            
            # Ordenar por relevancia
            colaboradores_ordenados = sorted(
                colaboradores.values(),
                key=lambda x: x['actividades_similares'],
                reverse=True
            )
            
            return colaboradores_ordenados[:5]
            
        except Exception as e:
            print(f"❌ Error sugiriendo colaboradores: {e}")
            return []