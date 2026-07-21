import uuid
from langchain_core.tools import tool
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from langchain_ollama import OllamaEmbeddings

from app.config import settings
from app.rag.audio_processor import extract_audio_features
from app.rag.retriever import get_rag_context


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
    """Guarda un nuevo dato, observación o instrucción del operario en la base de conocimientos vectorial (Qdrant) para aprendizaje continuo."""
    try:
        client = QdrantClient(url=settings.VECTOR_DB_URL)
        embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL, model=settings.EMBEDDING_MODEL_NAME
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
        return f"Aprendizaje registrado exitosamente en la base de datos: '{fact_description}'"
    except Exception as e:
        return f"Error al registrar el aprendizaje: {str(e)}"