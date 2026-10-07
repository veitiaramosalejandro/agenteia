import os
import sys
from datetime import datetime

# Agregar el directorio actual al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qdrant_client import QdrantClient
from app.config import settings
from app.rag.retriever import get_rag_context


def inspeccionar_qdrant():
    """Inspecciona el contenido de Qdrant para verificar que el agente lo está usando."""
    
    print("\n" + "="*60)
    print("🔍 INSPECCIONANDO BASE DE DATOS VECTORIAL (QDRANT)")
    print("="*60 + "\n")
    
    try:
        # Conectar a Qdrant
        client = QdrantClient(url=settings.VECTOR_DB_URL)
        collection = settings.VECTOR_COLLECTION_NAME
        
        # 1. Verificar estado de la colección
        print("📊 1. ESTADO DE LA COLECCIÓN:")
        try:
            collection_info = client.get_collection(collection_name=collection)
            print(f"   ✅ Colección: {collection}")
            print(f"   📄 Puntos totales: {collection_info.vectors_count}")
            print(f"   📦 Segmentos: {collection_info.segments_count}")
            print(f"   💾 Tamaño en disco: {collection_info.disk_data_size / 1024:.2f} KB")
        except Exception as e:
            print(f"   ❌ Error obteniendo información: {e}")
            print("   La colección podría no existir aún.")
            return
        
        print("\n📄 2. MUESTRA DE PUNTOS (primeros 10):")
        try:
            # Obtener una muestra de puntos
            points = client.scroll(
                collection_name=collection,
                limit=10,
                with_payload=True,
                with_vectors=False
            )[0]
            
            if not points:
                print("   ⚠️ No hay puntos en la colección")
                print("   El agente aún no ha aprendido nada.")
                return
            
            for i, point in enumerate(points, 1):
                print(f"\n   📌 Punto {i}:")
                print(f"      ID: {point.id}")
                
                if point.payload:
                    # Mostrar contenido del payload
                    content = point.payload.get("page_content", "")
                    if content:
                        # Mostrar primeros 200 caracteres
                        preview = content[:200] + "..." if len(content) > 200 else content
                        print(f"      Contenido: {preview}")
                    
                    # Mostrar otras claves importantes
                    for key in ["source", "category", "entity_type", "topic"]:
                        if key in point.payload:
                            print(f"      {key}: {point.payload[key]}")
        except Exception as e:
            print(f"   ❌ Error obteniendo puntos: {e}")
        
        print("\n🔎 3. PRUEBA DE CONSULTA (RAG):")
        # Hacer una consulta de prueba
        consultas = [
            "¿Qué información hay sobre clientes?",
            "¿Qué dice sobre la máquina CNC?",
            "Información sobre mantenimiento"
        ]
        
        for consulta in consultas:
            print(f"\n   📝 Consulta: '{consulta}'")
            try:
                resultado = get_rag_context(consulta, limit=2)
                if resultado and "No se encontró" not in resultado:
                    print(f"   ✅ Encontrado: {resultado[:200]}...")
                else:
                    print(f"   ⚠️ No se encontró información relevante")
            except Exception as e:
                print(f"   ❌ Error en la consulta: {e}")
        
        print("\n" + "="*60)
        print("✅ INSPECCIÓN COMPLETADA")
        print("="*60)
        
    except Exception as e:
        print(f"❌ Error general: {e}")
        print("   Verifica que Qdrant esté corriendo en:", settings.VECTOR_DB_URL)


if __name__ == "__main__":
    inspeccionar_qdrant()