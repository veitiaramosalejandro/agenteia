"""
app/rag/retriever.py - Recuperador de contexto RAG
"""

from qdrant_client import QdrantClient
from langchain_ollama import OllamaEmbeddings
from app.config import settings


def get_rag_context(query: str, limit: int = 3) -> str:
    """
    Consulta la base vectorial Qdrant usando embeddings locales de Ollama.
    """
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

        # ✅ CORREGIDO: Usar query_points en lugar de search
        try:
            # Intentar con el método nuevo
            search_result = client.query_points(
                collection_name=settings.VECTOR_COLLECTION_NAME,
                query=query_vector,
                limit=limit
            )
            results = search_result.points if hasattr(search_result, 'points') else search_result
        except AttributeError:
            # Fallback para versiones antiguas
            search_result = client.search(
                collection_name=settings.VECTOR_COLLECTION_NAME,
                query_vector=query_vector,
                limit=limit
            )
            results = search_result

        # Extraer el contexto de los resultados
        context_list = []
        for hit in results:
            if hasattr(hit, 'payload') and hit.payload:
                content = hit.payload.get("page_content", "")
                if content:
                    context_list.append(content)
            elif isinstance(hit, dict) and 'payload' in hit:
                content = hit.get('payload', {}).get("page_content", "")
                if content:
                    context_list.append(content)

        return "\n\n---\n\n".join(context_list) if context_list else "No se encontró información relevante en los manuales."
        
    except Exception as e:
        return f"Error al consultar el contexto RAG: {str(e)}"