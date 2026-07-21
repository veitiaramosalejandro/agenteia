import uuid
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from langchain_ollama import OllamaEmbeddings
from app.config import settings

def guardar_conversacion_como_conocimiento(session_id: str, tema: str, resumen_dialogo: str):
    """Guarda un texto de conversación mantenido entre el operario y el agente en Qdrant."""
    client = QdrantClient(url=settings.VECTOR_DB_URL)
    embeddings = OllamaEmbeddings(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.EMBEDDING_MODEL_NAME
    )
    
    contenido_vectorial = f"Conversación sobre {tema}: {resumen_dialogo}"
    vector = embeddings.embed_query(contenido_vectorial)

    point = PointStruct(
        id=str(uuid.uuid4()),
        vector=vector,
        payload={
            "page_content": contenido_vectorial,
            "session_id": session_id,
            "source": "historial_conversacion_operario"
        }
    )

    client.upsert(
        collection_name=settings.VECTOR_COLLECTION_NAME,
        points=[point]
    )
    print(f"✅ Conversación indexada en el RAG para aprendizaje futuro.")