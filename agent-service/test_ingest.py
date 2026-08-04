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
    print("\n" + "="*60)
    print("🧪 PROBANDO INGESTA SIMPLIFICADA")
    print("="*60 + "\n")
    
    # 1. Conectar a Qdrant
    print("📡 Conectando a Qdrant...")
    client = QdrantClient(url=settings.VECTOR_DB_URL)
    
    # 2. Verificar colección
    collection = settings.VECTOR_COLLECTION_NAME
    print(f"📊 Colección: {collection}")
    
    try:
        info = client.get_collection(collection)
        print(f"   Puntos actuales: {info.points_count}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return
    
    # 3. Conectar a Ollama
    print("\n🔧 Conectando a Ollama...")
    embeddings = OllamaEmbeddings(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.EMBEDDING_MODEL_NAME
    )
    
    # 4. Crear un documento de prueba
    print("\n📝 Creando documento de prueba...")
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
    print("🔄 Generando embedding...")
    try:
        vector = embeddings.embed_query(test_text)
        print(f"   ✅ Embedding generado (tamaño: {len(vector)})")
    except Exception as e:
        print(f"   ❌ Error generando embedding: {e}")
        return
    
    # 6. Crear punto
    point_id = str(uuid.uuid4())
    print(f"📌 Creando punto con ID: {point_id}")
    
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
    print("💾 Insertando en Qdrant...")
    try:
        client.upsert(
            collection_name=collection,
            points=[point]
        )
        print("   ✅ Insertado exitosamente")
    except Exception as e:
        print(f"   ❌ Error insertando: {e}")
        return
    
    # 8. Verificar resultado
    print("\n🔍 Verificando resultado...")
    info = client.get_collection(collection)
    print(f"   ✅ Puntos totales: {info.points_count}")
    
    # 9. Probar búsqueda
    print("\n🔎 Probando búsqueda...")
    try:
        # Buscar por el texto de prueba
        results = client.search(
            collection_name=collection,
            query_vector=vector,
            limit=1
        )
        if results:
            print(f"   ✅ Encontrado: {results[0].payload.get('page_content', '')[:100]}...")
        else:
            print("   ⚠️ No se encontraron resultados")
    except Exception as e:
        print(f"   ❌ Error en búsqueda: {e}")
    
    print("\n" + "="*60)
    print("✅ PRUEBA COMPLETADA")
    print("="*60)


if __name__ == "__main__":
    test_ingest()