import os
import sys
import uuid

# Agregar el directorio actual al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from langchain_ollama import OllamaEmbeddings
from app.config import settings


def test_ingest():
    pass
    pass
    pass
    
    # 1. Conectar a Qdrant
    pass
    client = QdrantClient(url=settings.VECTOR_DB_URL)
    
    # 2. Verificar colección
    collection = settings.VECTOR_COLLECTION_NAME
    pass
    
    try:
        info = client.get_collection(collection)
        pass
    except Exception as e:
        pass
        return
    
    # 3. Conectar a Ollama
    pass
    embeddings = OllamaEmbeddings(
        base_url=settings.EMBEDDING_BASE_URL,
        model=settings.EMBEDDING_MODEL_NAME
    )
    
    # 4. Crear un documento de prueba
    pass
    test_text = """
    DOCUMENTO DE PRUEBA
    
    Este es un documento de prueba para verificar que la ingesta funciona.
    
    Información de clientes:
    - Cliente 1: Industrias Metálicas SA - Bogotá
    - Cliente 2: Autopartes del Caribe - Barranquilla
    - Cliente 3: Construcciones Industriales - Medellín
    
    Máquinas CNC:
    - Modelo: HARTFORD VMC-1000
    - Velocidad: 0-8000 RPM
    - Potencia: 15 HP
    """
    
    # 5. Generar embedding
    pass
    try:
        vector = embeddings.embed_query(test_text)
        pass
    except Exception as e:
        pass
        return
    
    # 6. Crear punto
    point_id = str(uuid.uuid4())
    pass
    
    point = PointStruct(
        id=point_id,
        vector=vector,
        payload={
            "page_content": test_text,
            "source": "test_ingest",
            "timestamp": "2024-01-01T00:00:00"
        }
    )
    
    # 7. Insertar en Qdrant
    pass
    try:
        client.upsert(
            collection_name=collection,
            points=[point]
        )
        pass
    except Exception as e:
        pass
        return
    
    # 8. Verificar resultado
    pass
    info = client.get_collection(collection)
    pass
    
    # 9. Probar búsqueda
    pass
    try:
        # Buscar por el texto de prueba
        results = client.search(
            collection_name=collection,
            query_vector=vector,
            limit=1
        )
        if results:
            pass
        else:
            pass
    except Exception as e:
        pass
    
    pass
    pass
    pass


if __name__ == "__main__":
    test_ingest()
