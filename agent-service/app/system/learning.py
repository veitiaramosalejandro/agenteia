import uuid
import hashlib
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
        self.qdrant = QdrantClient(url=settings.VECTOR_DB_URL)
        self.collection = settings.VECTOR_COLLECTION_NAME
        self._ensure_collection()

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

    # ============================================================
    # 1. OBTENER CONTEXTO DEL USUARIO
    # ============================================================
    
    def obtener_contexto_usuario(self, user_id: str) -> Optional[ContextoUsuario]:
        """
        Obtiene todo el contexto de un usuario para personalizar respuestas.
        """
        # Conectar a SQL Server para obtener datos del usuario
        try:
            conn = pymssql.connect(
                server=settings.SQL_SERVER_HOST,
                user=settings.SQL_SERVER_USER,
                password=settings.SQL_SERVER_PASSWORD,
                database=settings.SQL_SERVER_DB,
                timeout=5,
            )
            cursor = conn.cursor(as_dict=True)
            
            # Obtener datos del recurso humano
            cursor.execute("""
                SELECT IDRecurso, Nombre, Email, Rol, Departamento, Especialidades
                FROM dbo.RecursosHumanos
                WHERE IDRecurso = %s
            """, (user_id,))
            usuario_data = cursor.fetchone()
            
            if not usuario_data:
                return None
            
            usuario = RecursoHumano(
                id=usuario_data["IDRecurso"],
                nombre=usuario_data["Nombre"],
                email=usuario_data["Email"],
                rol=usuario_data["Rol"],
                departamento=usuario_data.get("Departamento"),
                especialidades=usuario_data.get("Especialidades", "").split(",") if usuario_data.get("Especialidades") else [],
                canales=[]
            )
            
            # Obtener canales del usuario
            cursor.execute("""
                SELECT c.IDCanal, c.Nombre, c.Descripcion, c.Tipo
                FROM dbo.Canales c
                INNER JOIN dbo.RecursosHumanosCanales rc ON c.IDCanal = rc.IDCanal
                WHERE rc.IDRecurso = %s AND rc.Activo = 1
            """, (user_id,))
            canales_data = cursor.fetchall()
            
            canales = []
            for c in canales_data:
                canal = Canal(
                    id=c["IDCanal"],
                    nombre=c["Nombre"],
                    descripcion=c["Descripcion"],
                    tipo=c["Tipo"],
                    recursos_humanos=[],
                    recursos_materiales=[]
                )
                
                # Obtener recursos humanos del canal (para que el agente sepa con quién colabora)
                cursor.execute("""
                    SELECT IDRecurso FROM dbo.RecursosHumanosCanales
                    WHERE IDCanal = %s AND Activo = 1
                """, (c["IDCanal"],))
                canal.recursos_humanos = [r["IDRecurso"] for r in cursor.fetchall()]
                
                # Obtener recursos materiales del canal
                cursor.execute("""
                    SELECT IDMaterial, Nombre, Tipo, Estado
                    FROM dbo.RecursosMateriales
                    WHERE IDCanal = %s AND Activo = 1
                """, (c["IDCanal"],))
                materiales = cursor.fetchall()
                canal.recursos_materiales = [m["IDMaterial"] for m in materiales]
                
                canales.append(canal)
                usuario.canales.append(canal.id)
            
            # Obtener actividades recientes del usuario (últimos 7 días)
            cursor.execute("""
                SELECT IDActividad, IDCanal, Tipo, Descripcion, Fecha, Metadatos
                FROM dbo.Actividades
                WHERE IDRecurso = %s AND Fecha > DATEADD(day, -7, GETDATE())
                ORDER BY Fecha DESC
            """, (user_id,))
            actividades_data = cursor.fetchall()
            
            actividades = [
                Actividad(
                    id=a["IDActividad"],
                    recurso_humano_id=user_id,
                    canal_id=a["IDCanal"],
                    tipo=a["Tipo"],
                    descripcion=a["Descripcion"],
                    timestamp=a["Fecha"],
                    metadatos=a["Metadatos"] if a.get("Metadatos") else {}
                ) for a in actividades_data
            ]
            
            # Obtener recursos materiales disponibles para el usuario (en sus canales)
            recursos_disponibles = []
            for canal in canales:
                cursor.execute("""
                    SELECT IDMaterial, Nombre, Tipo, Estado, Especificaciones
                    FROM dbo.RecursosMateriales
                    WHERE IDCanal = %s AND Estado = 'disponible'
                """, (canal.id,))
                materiales = cursor.fetchall()
                for m in materiales:
                    recursos_disponibles.append(
                        RecursoMaterial(
                            id=m["IDMaterial"],
                            nombre=m["Nombre"],
                            tipo=m["Tipo"],
                            canal_id=canal.id,
                            estado=m["Estado"],
                            especificaciones=m["Especificaciones"] if m.get("Especificaciones") else {}
                        )
                    )
            
            conn.close()
            
            # Determinar permisos según rol
            permisos = self._obtener_permisos_por_rol(usuario.rol)
            
            return ContextoUsuario(
                usuario=usuario,
                canales_acceso=canales,
                actividades_recientes=actividades,
                recursos_disponibles=recursos_disponibles,
                permisos=permisos
            )
            
        except Exception as e:
            print(f"⚠️ Esquema primario no disponible para contexto de usuario: {e}")
            return self._obtener_contexto_usuario_fallback(user_id)

    def _obtener_contexto_usuario_fallback(self, user_id: str) -> Optional[ContextoUsuario]:
        """
        Fallback para esquemas donde no existen las tablas dbo.Recursos* y dbo.Canales.

        Mapeo rápido aplicado:
        - RecursoHumano -> dbo.SysPerson (IDResource, accountname/firstname/lastname, email, departament, title)
        - Canales usuario -> dbo.ActivitiesSysResources + dbo.Activity2Channel (IDChannel)
        - Actividades recientes -> dbo.ActivitiesSysResources + dbo.Activity
        - Recursos materiales -> dbo.Asset2SysResource + dbo.Asset2Channel (IDAsset)
        """
        try:
            conn = pymssql.connect(
                server=settings.SQL_SERVER_HOST,
                user=settings.SQL_SERVER_USER,
                password=settings.SQL_SERVER_PASSWORD,
                database=settings.SQL_SERVER_DB,
                timeout=5,
            )
            cursor = conn.cursor(as_dict=True)

            cursor.execute(
                """
                SELECT TOP 1
                    IDResource,
                    accountname,
                    firstname,
                    lastname,
                    email,
                    departament,
                    title
                FROM dbo.SysPerson
                WHERE IDResource = TRY_CONVERT(uniqueidentifier, %s)
                   OR organization_no = %s
                   OR reference = %s
                """,
                (user_id, user_id, user_id),
            )
            usuario_data = cursor.fetchone()
            if not usuario_data:
                conn.close()
                return None

            nombre = (
                usuario_data.get("accountname")
                or f"{(usuario_data.get('firstname') or '').strip()} {(usuario_data.get('lastname') or '').strip()}".strip()
                or str(usuario_data.get("IDResource") or user_id)
            )
            rol = (usuario_data.get("title") or "operario").strip() if usuario_data.get("title") else "operario"
            especialidades = [rol] if rol else []

            usuario = RecursoHumano(
                id=str(usuario_data.get("IDResource") or user_id),
                nombre=nombre,
                email=(usuario_data.get("email") or ""),
                rol=rol,
                departamento=usuario_data.get("departament"),
                especialidades=especialidades,
                canales=[],
            )

            cursor.execute(
                """
                SELECT DISTINCT TOP 30 a2c.IDChannel
                FROM dbo.ActivitiesSysResources asr
                INNER JOIN dbo.Activity2Channel a2c ON a2c.IDActivity = asr.IDActivity
                WHERE asr.IDResource = TRY_CONVERT(uniqueidentifier, %s)
                """,
                (user_id,),
            )
            canales_rows = cursor.fetchall() or []

            canales = []
            for row in canales_rows:
                canal_id = str(row.get("IDChannel")) if row.get("IDChannel") else None
                if not canal_id:
                    continue
                canal = Canal(
                    id=canal_id,
                    nombre=f"Canal {canal_id[:8]}",
                    descripcion="Canal inferido desde Activity2Channel",
                    tipo="operativo",
                    recursos_humanos=[],
                    recursos_materiales=[],
                )
                canales.append(canal)
                usuario.canales.append(canal_id)

            cursor.execute(
                """
                SELECT TOP 20
                    a.IDActivity,
                    a2c.IDChannel,
                    a.type,
                    a.subject,
                    a.description,
                    a.startDate
                FROM dbo.ActivitiesSysResources asr
                INNER JOIN dbo.Activity a ON a.IDActivity = asr.IDActivity
                LEFT JOIN dbo.Activity2Channel a2c ON a2c.IDActivity = a.IDActivity
                WHERE asr.IDResource = TRY_CONVERT(uniqueidentifier, %s)
                ORDER BY a.startDate DESC
                """,
                (user_id,),
            )
            actividades_rows = cursor.fetchall() or []

            actividades = []
            for a in actividades_rows:
                tipo = str(a.get("type")) if a.get("type") is not None else "actividad"
                descripcion = f"{(a.get('subject') or '').strip()} {(a.get('description') or '').strip()}".strip()
                timestamp = a.get("startDate") or datetime.now()
                actividades.append(
                    Actividad(
                        id=str(a.get("IDActivity")),
                        recurso_humano_id=str(usuario.id),
                        canal_id=str(a.get("IDChannel")) if a.get("IDChannel") else "canal_general",
                        tipo=tipo,
                        descripcion=descripcion or "Sin descripción",
                        timestamp=timestamp,
                        metadatos={},
                    )
                )

            cursor.execute(
                """
                SELECT DISTINCT TOP 30
                    a2c.IDChannel,
                    a2sr.IDAsset
                FROM dbo.Asset2SysResource a2sr
                LEFT JOIN dbo.Asset2Channel a2c ON a2c.IDAsset = a2sr.IDAsset
                WHERE a2sr.IDResource = TRY_CONVERT(uniqueidentifier, %s)
                  AND (a2sr.Active = 1 OR a2sr.Active IS NULL)
                """,
                (user_id,),
            )
            materiales_rows = cursor.fetchall() or []
            recursos_disponibles = []
            for m in materiales_rows:
                asset_id = m.get("IDAsset")
                if asset_id is None:
                    continue
                canal_id = str(m.get("IDChannel")) if m.get("IDChannel") else "canal_general"
                recursos_disponibles.append(
                    RecursoMaterial(
                        id=str(asset_id),
                        nombre=f"Asset {asset_id}",
                        tipo="asset",
                        canal_id=canal_id,
                        estado="disponible",
                        especificaciones={},
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
    
    # ============================================================
    # 2. GENERAR CONTEXTO PARA EL AGENTE (en texto)
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
    # 3. APRENDER DE LAS ACTIVIDADES (RAG)
    # ============================================================
    
    def aprender_actividad(self, actividad: Actividad) -> bool:
        """
        Aprende de una actividad realizada por un usuario.
        Indexa el conocimiento en Qdrant para futuras consultas.
        """
        try:
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
            
            # Generar embedding
            vector = self.embeddings.embed_query(texto_aprendizaje)
            
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
            
            vector = self.embeddings.embed_query(texto_canal)
            
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
    # 4. CONSULTAR CONOCIMIENTO APRENDIDO (para el agente)
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
            query_vector = self.embeddings.embed_query(query)

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
    # 5. SUGERIR COLABORADORES (basado en actividades pasadas)
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