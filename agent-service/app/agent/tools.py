import json
import os
from typing import Optional, Union
import uuid

import httpx
from langchain_core.tools import tool
from langchain_ollama import OllamaEmbeddings
import pymssql
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from app.config import settings
from app.rag.audio_processor import extract_audio_features
from app.rag.retriever import get_rag_context


# ---------------------------------------------------------------------------
# 1. TOOL: SQL Server Query
# ---------------------------------------------------------------------------
@tool
def query_sql_server(query: str) -> str:
    """Ejecuta una consulta SELECT en la base de datos SQL Server de la planta (ISIFrameIsicom).

    USAR SOLO CUANDO: Se requiera consultar historial de clientes/cuentas (dbo.Account),
    actividades/mantenimientos (dbo.Activity), equipos/activos (dbo.Asset) o
    parámetros almacenados en la base de datos.

    Parámetros:
        query (str): Consulta SQL SELECT a ejecutar.
    """
    server = os.getenv("SQL_SERVER_HOST", "172.16.10.149")
    user = os.getenv("SQL_SERVER_USER", "sa")
    password = os.getenv("SQL_SERVER_PASSWORD", "Abcd*1234")
    database = os.getenv("SQL_SERVER_DB", "ISIFrameIsicom")

    clean_query = query.strip()
    if not clean_query.upper().startswith("SELECT") and not clean_query.upper().startswith("WITH"):
        return "Error de seguridad: Solo se permiten consultas de lectura (SELECT / WITH)."

    forbidden_keywords = ["DELETE", "INSERT", "UPDATE", "DROP", "ALTER", "TRUNCATE", "EXEC", "EXECUTE"]
    if any(kw in clean_query.upper() for kw in forbidden_keywords):
        return "Error de seguridad: La consulta contiene comandos no permitidos para operaciones de lectura."

    try:
        conn = pymssql.connect(
            server=server,
            user=user,
            password=password,
            database=database,
            timeout=5,
        )
        cursor = conn.cursor(as_dict=True)
        cursor.execute(clean_query)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "La consulta se ejecutó correctamente pero no devolvió resultados."

        return str(rows[:15])

    except pymssql.Error as db_err:
        return f"Error SQL Server: {str(db_err)}. Ajusta los campos/tablas y vuelve a intentar."
    except Exception as e:
        return f"Error al conectar o consultar SQL Server: {str(e)}"


@tool
def get_db_schema(table_name: Optional[str] = None) -> str:
    """Consulta los metadatos de la base de datos SQL Server.
    
    USAR CUANDO: El usuario pregunte qué tablas existen, qué columnas tiene una tabla específica,
    o para explorar la estructura de la base de datos antes de hacer un SELECT.
    
    Parámetros:
        table_name (str, opcional): Si se proporciona, devuelve las columnas y tipos de datos
                                   de esa tabla. Si es None, devuelve la lista de todas las tablas.
    """
    server = os.getenv("SQL_SERVER_HOST", "172.16.10.149")
    user = os.getenv("SQL_SERVER_USER", "sa")
    password = os.getenv("SQL_SERVER_PASSWORD", "Abcd*1234")
    database = os.getenv("SQL_SERVER_DB", "ISIFrameIsicom")

    try:
        conn = pymssql.connect(server=server, user=user, password=password, database=database, timeout=5)
        cursor = conn.cursor(as_dict=True)

        if table_name:
            # Obtener columnas y tipos de datos de una tabla específica
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
            return f"Columnas de {table_name}: " + str(rows)
        else:
            # Obtener todas las tablas disponibles en la BD
            query = """
                SELECT TABLE_NAME 
                FROM INFORMATION_SCHEMA.TABLES 
                WHERE TABLE_TYPE = 'BASE TABLE'
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()
            tables = [r['TABLE_NAME'] for r in rows]
            return f"Tablas disponibles en la base de datos ({len(tables)} en total): " + ", ".join(tables)

    except Exception as e:
        return f"Error al consultar el esquema: {str(e)}"


# ---------------------------------------------------------------------------
# 2. TOOL: Consumir API Externa
# ---------------------------------------------------------------------------
@tool
def fetch_external_api(
    endpoint_url: str,
    method: str = "GET",
    payload: Optional[Union[dict, str]] = None,
) -> str:
    """Realiza una petición HTTP/GraphQL a una API externa."""
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
# 3. TOOLS: Telemetría y Diagnóstico CNC
# ---------------------------------------------------------------------------
@tool
def get_cnc_telemetry() -> dict:
    """Consulta la telemetría en tiempo real de la máquina CNC (RPM, temperatura, motor, alarmas)."""
    return {
        "status": "OPERATIONAL",
        "spindle_speed_rpm": 3200,
        "feed_rate_mm_min": 450,
        "spindle_power_pct": 88.5,
        "active_alarms": ["ALARM_102: Overload Spindle Warning"],
    }


@tool
def recommend_cnc_action(action: str, parameter: str, value: str) -> str:
    """Envía una recomendación de acción correctiva para la máquina CNC."""
    return f"Acción '{action}' con parámetro '{parameter}={value}' registrada."


@tool
def analyze_pcm_audio_diagnostic(file_path: str) -> str:
    """Procesa y diagnostica un archivo de audio .pcm de la máquina CNC HARTFORD."""
    try:
        features = extract_audio_features(file_path)
        rag_matches = get_rag_context(features["text_summary"])
        return (
            f"Resultados del análisis acústico para {features['file_name']}:\n"
            f"- Energía RMS: {features['rms_energy']:.4f}\n"
            f"- Centroide Espectral: {features['spectral_centroid']:.2f} Hz\n"
            f"- Coincidencias RAG:\n{rag_matches}"
        )
    except Exception as e:
        return f"Error al procesar el archivo de audio: {str(e)}"


@tool
def learn_new_fact(fact_description: str, category: str = "general") -> str:
    """Guarda un nuevo dato u observación en la base de conocimientos vectorial (Qdrant)."""
    try:
        client = QdrantClient(url=settings.VECTOR_DB_URL)
        embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME,
        )

        vector = embeddings.embed_query(fact_description)

        point = PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "page_content": fact_description,
                "category": category,
                "source": "operator_learning",
            },
        )

        client.upsert(collection_name=settings.VECTOR_COLLECTION_NAME, points=[point])
        return f"Aprendizaje registrado correctamente: '{fact_description}'"
    except Exception as e:
        return f"Error al registrar el aprendizaje: {str(e)}"