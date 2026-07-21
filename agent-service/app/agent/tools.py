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
    """Ejecuta una consulta SELECT en la base de datos SQL Server de la planta.
    
    USAR SOLO CUANDO: El usuario pida explícitamente consultar registros históricos,
    tablas de producción, canales o datos persistidos en la base de datos.
    """
    server = os.getenv("SQL_SERVER_HOST", "sql-server")
    user = os.getenv("SQL_SERVER_USER", "sa")
    password = os.getenv("SQL_SERVER_PASSWORD", "YourPassword123!")
    database = os.getenv("SQL_SERVER_DB", "CNC_Factory")

    clean_query = query.strip()
    if not clean_query.upper().startswith("SELECT"):
        return "Error: Solo se permiten consultas de lectura (SELECT)."

    try:
        conn = pymssql.connect(
            server=server, user=user, password=password, database=database
        )
        cursor = conn.cursor(as_dict=True)
        cursor.execute(clean_query)
        rows = cursor.fetchall()
        conn.close()

        return str(rows[:20]) if rows else "La consulta no devolvió resultados."

    except Exception as e:
        return f"Error ejecutando la consulta en SQL Server: {str(e)}"


# ---------------------------------------------------------------------------
# 2. TOOL: Consumir API Externa
# ---------------------------------------------------------------------------
@tool
def fetch_external_api(
    endpoint_url: str,
    method: str = "GET",
    payload: Optional[Union[dict, str]] = None,
) -> str:
    """Realiza una petición HTTP/GraphQL a una API externa para obtener o aprender de sus datos.
    
    USAR SOLO CUANDO: El usuario proporcione o solicite explícitamente consultar una URL/API externa.
    
    Parámetros:
        endpoint_url (str): La URL completa del endpoint de la API.
        method (str): El método HTTP ("GET" o "POST"). Por defecto "GET".
        payload (dict o str): Datos JSON para enviar en caso de peticiones POST.
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
        return f"Error HTTP {exc.response.status_code} al consultar la API: {exc.response.text}"
    except Exception as e:
        return f"Error de conexión con la API: {str(e)}"


# ---------------------------------------------------------------------------
# 3. TOOL: Telemetría CNC
# ---------------------------------------------------------------------------
@tool
def get_cnc_telemetry() -> dict:
    """Consulta la telemetría en tiempo real de la máquina CNC (RPM, temperatura, estado del motor, alarmas).

    USAR SOLO CUANDO: El usuario pida explícitamente ver el estado, parámetros,
    rendimiento o telemetría actual de la máquina CNC. NO usar si el usuario
    está haciendo preguntas generales o teóricas.
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
    """Envía una recomendación de acción correctiva para la máquina CNC."""
    return f"Acción '{action}' con parámetro '{parameter}={value}' registrada y enviada al panel web del operario."


@tool
def analyze_pcm_audio_diagnostic(file_path: str) -> str:
    """Procesa y diagnostica un archivo de audio .pcm proveniente de los sensores de la máquina CNC HARTFORD."""
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
    """Guarda un nuevo dato, observación o instrucción del operario en la base de conocimientos vectorial (Qdrant).
    Funciona para textos en Español, Portugués e Inglés.
    """
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

        client.upsert(
            collection_name=settings.VECTOR_COLLECTION_NAME, points=[point]
        )
        return f"Aprendizaje registrado / Learning registered / Aprendizado registrado: '{fact_description}'"
    except Exception as e:
        return f"Error al registrar el aprendizaje: {str(e)}"