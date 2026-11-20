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
    pass
    pass
    pass
    
    # Conectar a Qdrant
    pass
    client = QdrantClient(url=settings.VECTOR_DB_URL)
    collection = settings.VECTOR_COLLECTION_NAME
    
    # Verificar colección
    try:
        info = client.get_collection(collection)
        pass
    except Exception as e:
        pass
        return
    
    # Conectar a Ollama
    pass
    try:
        embeddings = OllamaEmbeddings(
            base_url=settings.EMBEDDING_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME
        )
        # Probar conexión
        test_vector = embeddings.embed_query("test")
        pass
    except Exception as e:
        pass
        return
    
    # Buscar archivos
    knowledge_dir = os.path.join(os.path.dirname(__file__), "knowledge_base")
    
    if not os.path.exists(knowledge_dir):
        pass
        pass
        return
    
    # Obtener todos los archivos .txt
    files = []
    for f in os.listdir(knowledge_dir):
        if f.endswith('.txt'):
            files.append(os.path.join(knowledge_dir, f))
    
    if not files:
        pass
        return
    
    pass
    for f in files:
        pass
    
    total_points = 0
    
    for file_path in files:
        pass
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            pass
        except Exception as e:
            pass
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
        
        pass
        
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
                pass
                continue
        
        if points:
            try:
                client.upsert(
                    collection_name=collection,
                    points=points
                )
                pass
                total_points += len(points)
            except Exception as e:
                pass
    
    # Mostrar resultado final
    pass
    pass
    pass
    
    info = client.get_collection(collection)
    pass
    pass
    
    pass
    pass


if __name__ == "__main__":
    ingest_all_documents()
