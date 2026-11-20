"""
check_knowledge.py - Verifica el conocimiento del agente sobre la BD
Ejecutar: python check_knowledge.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qdrant_client import QdrantClient
from langchain_ollama import OllamaEmbeddings
from app.config import settings


def check_knowledge():
    """Verifica el conocimiento del agente sobre la base de datos."""
    
    pass
    pass
    pass
    
    try:
        client = QdrantClient(url=settings.VECTOR_DB_URL)
        embeddings = OllamaEmbeddings(
            base_url=settings.EMBEDDING_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME
        )
        
        # 1. Verificar puntos en la colección
        try:
            info = client.get_collection(settings.VECTOR_COLLECTION_NAME)
            # Usar el atributo correcto
            if hasattr(info, 'points_count'):
                count = info.points_count
            elif hasattr(info, 'vectors_count'):
                count = info.vectors_count
            else:
                count = "desconocido"
            pass
        except Exception as e:
            pass
        
        # 2. Hacer consultas de prueba
        pass
        
        queries = [
            ("Clientes", "¿Qué tabla contiene información de clientes?"),
            ("Account", "¿Qué campos tiene la tabla Account?"),
            ("Relaciones", "¿Cómo se relaciona Activity con Account?"),
            ("CNC", "¿Qué tablas existen para la configuración CNC?"),
            ("Asset", "¿Qué información contiene la tabla Asset?"),
            ("Database", "¿Cuál es la estructura de la base de datos ISIFrameIsicom?"),
        ]
        
        for label, query in queries:
            pass
            try:
                query_vector = embeddings.embed_query(query)
                
                # Intentar con query_points (nuevo)
                try:
                    result = client.query_points(
                        collection_name=settings.VECTOR_COLLECTION_NAME,
                        query=query_vector,
                        limit=2
                    )
                    points = result.points if hasattr(result, 'points') else result
                except (AttributeError, TypeError):
                    # Fallback a search (antiguo)
                    result = client.search(
                        collection_name=settings.VECTOR_COLLECTION_NAME,
                        query_vector=query_vector,
                        limit=2
                    )
                    points = result
                
                if points:
                    found_content = False
                    for hit in points:
                        payload = hit.payload if hasattr(hit, 'payload') else hit.get('payload', {})
                        content = payload.get("page_content", "")
                        if content and len(content) > 50:
                            preview = content[:150].replace('\n', ' ') + "..."
                            pass
                            found_content = True
                            break
                    if not found_content:
                        pass
                else:
                    pass
                    
            except Exception as e:
                pass
        
        # 3. Resumen
        pass
        pass
        pass
        
    except Exception as e:
        pass


if __name__ == "__main__":
    check_knowledge()
