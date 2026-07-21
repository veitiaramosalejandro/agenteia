from qdrant_client import QdrantClient
from langchain_ollama import OllamaEmbeddings
from app.config import settings

def get_rag_context(query: str, limit: int = 3) -> str:
    """Consulta la base vectorial Qdrant usando embeddings locales de Ollama."""
    try:
        embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME
        )
        query_vector = embeddings.embed_query(query)

        client = QdrantClient(url=settings.VECTOR_DB_URL)
        
        # Verificar si la colección existe
        collections = [c.name for c in client.get_collections().collections]
        if settings.VECTOR_COLLECTION_NAME not in collections:
            return "No hay documentos cargados en la base vectorial."

        search_result = client.search(
            collection_name=settings.VECTOR_COLLECTION_NAME,
            query_vector=query_vector,
            limit=limit
        )

        context_list = [hit.payload.get("page_content", "") for hit in search_result if hit.payload]
        return "\n\n---\n\n".join(context_list) if context_list else "No se encontró información relevante en los manuales."
    except Exception as e:
        return f"Error al consultar el contexto RAG: {str(e)}"