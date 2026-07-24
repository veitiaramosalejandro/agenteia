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
    
    print("\n" + "="*60)
    print("🔍 VERIFICANDO CONOCIMIENTO DE LA BASE DE DATOS")
    print("="*60 + "\n")
    
    try:
        client = QdrantClient(url=settings.VECTOR_DB_URL)
        embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
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
            print(f"📊 Puntos en colección: {count}")
        except Exception as e:
            print(f"⚠️ Error obteniendo información de colección: {e}")
        
        # 2. Hacer consultas de prueba
        print("\n📝 CONSULTAS DE PRUEBA:")
        
        queries = [
            ("Clientes", "¿Qué tabla contiene información de clientes?"),
            ("Account", "¿Qué campos tiene la tabla Account?"),
            ("Relaciones", "¿Cómo se relaciona Activity con Account?"),
            ("CNC", "¿Qué tablas existen para la configuración CNC?"),
            ("Asset", "¿Qué información contiene la tabla Asset?"),
            ("Database", "¿Cuál es la estructura de la base de datos ISIFrameIsicom?"),
        ]
        
        for label, query in queries:
            print(f"\n   🔍 {label}: '{query}'")
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
                            print(f"      ✅ Encontrado: {preview}")
                            found_content = True
                            break
                    if not found_content:
                        print("      ⚠️ No se encontró contenido relevante")
                else:
                    print("      ⚠️ No se encontraron resultados")
                    
            except Exception as e:
                print(f"      ❌ Error: {e}")
        
        # 3. Resumen
        print("\n" + "="*60)
        print("✅ VERIFICACIÓN COMPLETADA")
        print("="*60)
        
    except Exception as e:
        print(f"❌ Error general: {e}")


if __name__ == "__main__":
    check_knowledge()