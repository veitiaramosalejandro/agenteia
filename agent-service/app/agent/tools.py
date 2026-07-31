import json
import os
import re
from typing import Optional, Union
import uuid
import hashlib

import httpx
from langchain_core.tools import tool
from langchain_ollama import OllamaEmbeddings
import pymssql
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Distance, VectorParams

from app.config import settings
from app.rag.audio_processor import extract_audio_features
from app.rag.retriever import get_rag_context


def _extract_cte_names(query: str) -> set[str]:
    cte_names = set()
    for match in re.finditer(r"(?:WITH|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", query, flags=re.IGNORECASE):
        cte_names.add(match.group(1).lower())
    return cte_names


def _extract_table_references(query: str) -> set[str]:
    refs = set()
    cte_names = _extract_cte_names(query)
    for match in re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z0-9_\[\]\.]+)", query, flags=re.IGNORECASE):
        token = match.group(1).strip()
        if token.startswith("("):
            continue
        clean = token.replace("[", "").replace("]", "")
        parts = [p for p in clean.split(".") if p]
        if not parts:
            continue
        if len(parts) == 1:
            schema = "dbo"
            table = parts[0]
        else:
            schema = parts[-2]
            table = parts[-1]
        if table.lower() in cte_names:
            continue
        refs.add(f"{schema.lower()}.{table.lower()}")
    return refs


def _load_real_tables(cursor) -> set[str]:
    cursor.execute(
        """
        SELECT TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
        """
    )
    rows = cursor.fetchall() or []
    return {f"{str(r['TABLE_SCHEMA']).lower()}.{str(r['TABLE_NAME']).lower()}" for r in rows}


# ---------------------------------------------------------------------------
# 1. TOOL: SQL Server Query (MEJORADA con validación de filas)
# ---------------------------------------------------------------------------
@tool
def query_sql_server(query: str) -> str:
    """
    EJECUTA CONSULTAS SELECT EN SQL SERVER.
    
    CUÁNDO USARLA:
    - Cuando el usuario pida datos de clientes (dbo.Account)
    - Cuando el usuario pida historial de actividades (dbo.Activity)
    - Cuando el usuario pida información de máquinas/activos (dbo.Asset)
    - Cuando el usuario pregunte por saldos, deudas o inventarios
    
    CUÁNDO NO USARLA:
    - NO la uses para explorar la estructura de tablas (usa get_db_schema)
    - NO la uses para saludos o conversación general
    
    ADVERTENCIA DE SEGURIDAD:
    - Siempre usa filtros WHERE para evitar consultas masivas
    - Si el usuario no especifica filtros, pregunta antes de ejecutar
    """
    server = settings.SQL_SERVER_HOST
    user = settings.SQL_SERVER_USER
    password = settings.SQL_SERVER_PASSWORD
    database = settings.SQL_SERVER_DB

    clean_query = query.strip()
    if not clean_query.upper().startswith("SELECT") and not clean_query.upper().startswith("WITH"):
        return "Error de seguridad: Solo se permiten consultas de lectura (SELECT / WITH)."

    forbidden_keywords = ["DELETE", "INSERT", "UPDATE", "DROP", "ALTER", "TRUNCATE", "EXEC", "EXECUTE"]
    if any(kw in clean_query.upper() for kw in forbidden_keywords):
        return "Error de seguridad: La consulta contiene comandos no permitidos."

    try:
        conn = pymssql.connect(
            server=server,
            user=user,
            password=password,
            database=database,
            timeout=5,
        )
        cursor = conn.cursor(as_dict=True)

        referenced_tables = _extract_table_references(clean_query)
        real_tables = _load_real_tables(cursor)
        unknown_tables = sorted(t for t in referenced_tables if t not in real_tables)
        if unknown_tables:
            conn.close()
            unknown = ", ".join(unknown_tables)
            sample = ", ".join(sorted(list(real_tables))[:12])
            return (
                "Error de esquema: la consulta usa tablas que no existen en esta base de datos: "
                f"{unknown}. "
                "Consulta primero el esquema real con get_db_schema antes de reintentar. "
                f"Ejemplos de tablas reales: {sample}."
            )

        cursor.execute(clean_query)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "La consulta se ejecutó correctamente pero no devolvió resultados."

        # 🚨 NUEVA VALIDACIÓN: Si son más de 50 filas, advertir
        if len(rows) > 50:
            return f"⚠️ ADVERTENCIA: La consulta devolvió {len(rows)} filas. Mostrando solo las primeras 15.\n\n{json.dumps(rows[:15])}"

        return json.dumps(rows[:15])

    except pymssql.Error as db_err:
        return f"Error SQL Server: {str(db_err)}. Ajusta los campos/tablas y vuelve a intentar."
    except Exception as e:
        return f"Error al conectar o consultar SQL Server: {str(e)}"


@tool
def get_db_schema(table_name: Optional[str] = None) -> str:
    """
    EXPLORA LA ESTRUCTURA DE LA BASE DE DATOS.
    
    CUÁNDO USARLA (SOLO PARA EXPLORACIÓN):
    - El usuario pregunta "¿Qué tablas hay en la base de datos?"
    - El usuario pregunta "¿Qué columnas tiene la tabla X?"
    - El usuario dice "No sé qué tabla contiene los datos de clientes"
    
    CUÁNDO NO USARLA:
    - NO la uses para obtener datos reales de negocio (usa query_sql_server)
    - NO la uses si el usuario ya sabe qué tabla consultar
    """
    server = settings.SQL_SERVER_HOST
    user = settings.SQL_SERVER_USER
    password = settings.SQL_SERVER_PASSWORD
    database = settings.SQL_SERVER_DB

    try:
        conn = pymssql.connect(server=server, user=user, password=password, database=database, timeout=5)
        cursor = conn.cursor(as_dict=True)

        if table_name:
            query = """
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = %s
            """
            cursor.execute(query, (table_name,))
            rows = cursor.fetchall()
            conn.close()
            if not rows:
                return f"No se encontró la tabla '{table_name}'."
            # Formatear bonito
            resultado = f"📋 ESTRUCTURA DE LA TABLA '{table_name}':\n\n"
            for row in rows:
                resultado += f"  • {row['COLUMN_NAME']} ({row['DATA_TYPE']}) - {'Puede ser NULL' if row['IS_NULLABLE'] == 'YES' else 'NO NULL'}\n"
            return resultado
        else:
            query = """
                SELECT TABLE_NAME 
                FROM INFORMATION_SCHEMA.TABLES 
                WHERE TABLE_TYPE = 'BASE TABLE'
                ORDER BY TABLE_NAME
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()
            tables = [r['TABLE_NAME'] for r in rows]
            return f"📊 TABLAS DISPONIBLES ({len(tables)} en total):\n\n" + "\n".join([f"  • {t}" for t in tables[:30]])

    except Exception as e:
        return f"Error al consultar el esquema: {str(e)}"


# ---------------------------------------------------------------------------
# 🚨 NUEVA TOOL: Confirmación de consultas pesadas (Human-in-the-loop)
# ---------------------------------------------------------------------------
@tool
def confirm_large_operation(operation_type: str, description: str, estimated_impact: str) -> str:
    """
    SOLICITA CONFIRMACIÓN DEL USUARIO ANTES DE OPERACIONES CRÍTICAS.
    
    CUÁNDO USARLA (SIEMPRE antes de):
    - Ejecutar un SELECT sin filtros WHERE (puede devolver miles de filas)
    - Enviar correos o mensajes automáticos
    - Realizar cambios en configuración de máquinas
    - Cualquier acción que el usuario NO haya pedido explícitamente
    
    CÓMO USARLA:
    1. Describe la operación que quieres hacer
    2. Espera la respuesta del usuario (Sí/No)
    3. Si dice "Sí", ejecuta la acción; si dice "No", cancela
    """
    return f"⚠️ SOLICITUD DE CONFIRMACIÓN:\n\nOperación: {operation_type}\nDescripción: {description}\nImpacto estimado: {estimated_impact}\n\n❓ ¿Confirmas que quieres realizar esta operación? (Responde 'Sí' o 'No')"


# ---------------------------------------------------------------------------
# 2. TOOL: Consumir API Externa (MEJORADA)
# ---------------------------------------------------------------------------
@tool
def fetch_external_api(endpoint_url: str, method: str = "GET", payload: Optional[Union[dict, str]] = None,) -> str:
    """
    CONSULTA APIs EXTERNAS (HTTP/GraphQL).
    
    CUÁNDO USARLA:
    - El usuario comparte una URL y dice "consulta esto"
    - El usuario pide datos de un sistema externo (clima, cotizaciones, etc.)
    
    PRECAUCIÓN:
    - NUNCA la uses para APIs internas de la planta (usa query_sql_server)
    - Siempre valida que la URL sea segura
    """
    try:
        parsed_payload = None
        if payload:
            if isinstance(payload, str):
                try:
                    parsed_payload = json.loads(payload)
                except json.JSONDecodeError:
                    clean_str = payload.replace("'", '"')
                    parsed_payload = json.loads(clean_str)
            else:
                parsed_payload = payload

        with httpx.Client(timeout=15.0) as client:
            if method.upper() == "POST":
                response = client.post(endpoint_url, json=parsed_payload or {})
            else:
                response = client.get(endpoint_url)
                
            response.raise_for_status()
            return str(response.json())
            
    except httpx.HTTPStatusError as exc:
        return f"Error HTTP {exc.response.status_code}: {exc.response.text}"
    except Exception as e:
        return f"Error de conexión con la API: {str(e)}"


# ---------------------------------------------------------------------------
# 3. TOOLS: Telemetría y Diagnóstico CNC (MEJORADAS)
# ---------------------------------------------------------------------------
@tool
def get_cnc_telemetry() -> dict:
    """
    CONSULTA TELEMETRÍA EN TIEMPO REAL DE LA CNC HARTFORD.
    
    CUÁNDO USARLA:
    - El usuario pregunta "¿Cómo está la máquina?"
    - El usuario pregunta "¿Qué alarmas tiene?"
    - El usuario pregunta por RPM, temperatura o velocidad de avance
    
    DEVUELVE:
    - Estado de la máquina (OPERATIONAL/STOPPED/MAINTENANCE)
    - Velocidad del husillo (RPM)
    - Velocidad de avance (mm/min)
    - Potencia del husillo (%)
    - Alarmas activas
    """
    return {
        "status": "OPERATIONAL",
        "spindle_speed_rpm": 3200,
        "feed_rate_mm_min": 450,
        "spindle_power_pct": 88.5,
        "active_alarms": ["ALARM_102: Overload Spindle Warning"],
    }


@tool
def recommend_cnc_action(action: str, parameter: str, value: str) -> str:
    """
    REGISTRA UNA ACCIÓN CORRECTIVA PARA LA CNC.
    
    CUÁNDO USARLA:
    - Cuando el usuario pide cambiar un parámetro de la máquina
    - Cuando el usuario dice "Reduce la velocidad" o "Sube la temperatura"
    - Cuando se detecta una alarma y se necesita ajustar algo
    
    NOTA: Esta herramienta SOLO registra la acción, no la ejecuta físicamente.
    """
    return f"✅ Acción '{action}' con parámetro '{parameter}={value}' registrada en el sistema de mantenimiento."


@tool
def analyze_pcm_audio_diagnostic(file_path: str) -> str:
    """
    ANALIZA ARCHIVOS DE AUDIO .PCM DE LA CNC HARTFORD.
    
    CUÁNDO USARLA:
    - El usuario sube un archivo .pcm y pide diagnóstico
    - El usuario menciona "ruido extraño en la máquina"
    - El usuario pregunta por análisis acústico
    
    DEVUELVE:
    - Energía RMS (indica nivel de vibración)
    - Centroide espectral (frecuencia dominante)
    - Coincidencias con patrones conocidos (RAG)
    """
    try:
        features = extract_audio_features(file_path)
        rag_matches = get_rag_context(features["text_summary"])
        return (
            f"🔊 RESULTADOS DEL ANÁLISIS ACÚSTICO:\n\n"
            f"📁 Archivo: {features['file_name']}\n"
            f"📊 Energía RMS: {features['rms_energy']:.4f} (indica nivel de vibración)\n"
            f"🎵 Centroide Espectral: {features['spectral_centroid']:.2f} Hz\n"
            f"📚 Patrones coincidentes en la base de conocimiento:\n{rag_matches}"
        )
    except Exception as e:
        return f"Error al procesar el archivo de audio: {str(e)}"


@tool
def learn_new_fact(fact_description: str, category: str = "general") -> str:
    """
    GUARDA NUEVO CONOCIMIENTO EN LA BASE VECTORIAL.
    
    CUÁNDO USARLA:
    - El usuario dice "Aprende esto: ..."
    - El usuario comparte una observación importante sobre la máquina
    - El usuario enseña una regla de mantenimiento
    
    REGLAS:
    - SIEMPRE usa 'general' como categoría por defecto
    - Usa 'mantenimiento' si es sobre reparaciones
    - Usa 'operacion' si es sobre procedimientos de trabajo
    """
    try:
        client = QdrantClient(url=settings.VECTOR_DB_URL)
        embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME,
        )

        collections = [c.name for c in client.get_collections().collections]
        if settings.VECTOR_COLLECTION_NAME not in collections:
            client.create_collection(
                collection_name=settings.VECTOR_COLLECTION_NAME,
                vectors_config=VectorParams(size=768, distance=Distance.COSINE),
            )

        vector = embeddings.embed_query(fact_description)

        # 🚀 MEJORA: ID único basado en hash para evitar duplicados
        content_hash = hashlib.md5(fact_description.encode()).hexdigest()
        point_id = str(uuid.UUID(content_hash))

        point = PointStruct(
            id=point_id,
            vector=vector,
            payload={
                "page_content": fact_description,
                "category": category,
                "source": "operator_learning",
                "timestamp": str(uuid.uuid4()),
            },
        )

        client.upsert(collection_name=settings.VECTOR_COLLECTION_NAME, points=[point])
        return f"✅ Aprendizaje registrado correctamente: '{fact_description}'"
    except Exception as e:
        return f"Error al registrar el aprendizaje: {str(e)}"