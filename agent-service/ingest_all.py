"""
ingest_all.py - Ingesta masiva de todos los documentos en knowledge_base/
"""

import os
import sys
import uuid

# Agregar el directorio actual al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from langchain_ollama import OllamaEmbeddings
from app.config import settings


def ingest_all_documents():
    print("\n" + "="*60)
    print("📚 INGESTA MASIVA DE DOCUMENTOS")
    print("="*60 + "\n")
    
    # Conectar a Qdrant
    print("📡 Conectando a Qdrant...")
    client = QdrantClient(url=settings.VECTOR_DB_URL)
    collection = settings.VECTOR_COLLECTION_NAME
    
    # Verificar colección
    try:
        info = client.get_collection(collection)
        print(f"📊 Colección: {collection} (puntos actuales: {info.points_count})")
    except Exception as e:
        print(f"❌ Error: {e}")
        return
    
    # Conectar a Ollama
    print("\n🔧 Conectando a Ollama...")
    try:
        embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME
        )
        # Probar conexión
        test_vector = embeddings.embed_query("test")
        print(f"   ✅ Embeddings disponibles (tamaño: {len(test_vector)})")
    except Exception as e:
        print(f"   ❌ Error conectando a Ollama: {e}")
        return
    
    # Buscar archivos
    knowledge_dir = os.path.join(os.path.dirname(__file__), "knowledge_base")
    
    if not os.path.exists(knowledge_dir):
        print(f"\n❌ Directorio no encontrado: {knowledge_dir}")
        print("   Crea la carpeta 'knowledge_base' con tus documentos")
        return
    
    # Obtener todos los archivos .txt
    files = []
    for f in os.listdir(knowledge_dir):
        if f.endswith('.txt'):
            files.append(os.path.join(knowledge_dir, f))
    
    if not files:
        print(f"\n⚠️ No se encontraron archivos .txt en {knowledge_dir}")
        return
    
    print(f"\n📂 Encontrados {len(files)} archivos:")
    for f in files:
        print(f"   - {os.path.basename(f)}")
    
    total_points = 0
    
    for file_path in files:
        print(f"\n📄 Procesando: {os.path.basename(file_path)}")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            print(f"   📏 Tamaño: {len(content)} caracteres")
        except Exception as e:
            print(f"   ❌ Error leyendo: {e}")
            continue
        
        # Dividir en fragmentos por párrafos
        chunks = content.split('\n\n')
        chunks = [c.strip() for c in chunks if len(c.strip()) > 50]
        
        if not chunks:
            # Si no hay párrafos, dividir por líneas
            lines = content.split('\n')
            chunks = []
            current_chunk = ""
            for line in lines:
                if line.strip():
                    current_chunk += line + "\n"
                    if len(current_chunk) > 500:
                        chunks.append(current_chunk.strip())
                        current_chunk = ""
            if current_chunk:
                chunks.append(current_chunk.strip())
        
        print(f"   📝 {len(chunks)} fragmentos")
        
        points = []
        for i, chunk in enumerate(chunks):
            if len(chunk.strip()) < 50:
                continue
            
            try:
                # Generar embedding
                vector = embeddings.embed_query(chunk)
                
                # Crear punto con UUID
                point_id = str(uuid.uuid4())
                
                point = PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "page_content": chunk,
                        "source": os.path.basename(file_path),
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                        "file_type": "txt"
                    }
                )
                points.append(point)
            except Exception as e:
                print(f"   ⚠️ Error en fragmento {i}: {e}")
                continue
        
        if points:
            try:
                client.upsert(
                    collection_name=collection,
                    points=points
                )
                print(f"   ✅ Indexados {len(points)} fragmentos")
                total_points += len(points)
            except Exception as e:
                print(f"   ❌ Error insertando: {e}")
    
    # Mostrar resultado final
    print("\n" + "="*60)
    print(f"📊 RESUMEN FINAL")
    print("="*60)
    
    info = client.get_collection(collection)
    print(f"✅ Puntos totales en colección: {info.points_count}")
    print(f"✅ Nuevos puntos indexados: {total_points}")
    
    print("\n✅ ¡Ingesta completada exitosamente!")
    print("="*60)


if __name__ == "__main__":
    ingest_all_documents()