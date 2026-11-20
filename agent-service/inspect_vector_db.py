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
    
    pass
    pass
    pass
    
    try:
        # Conectar a Qdrant
        client = QdrantClient(url=settings.VECTOR_DB_URL)
        collection = settings.VECTOR_COLLECTION_NAME
        
        # 1. Verificar estado de la colección
        pass
        try:
            collection_info = client.get_collection(collection_name=collection)
            pass
            pass
            pass
            pass
        except Exception as e:
            pass
            pass
            return
        
        pass
        try:
            # Obtener una muestra de puntos
            points = client.scroll(
                collection_name=collection,
                limit=10,
                with_payload=True,
                with_vectors=False
            )[0]
            
            if not points:
                pass
                pass
                return
            
            for i, point in enumerate(points, 1):
                pass
                pass
                
                if point.payload:
                    # Mostrar contenido del payload
                    content = point.payload.get("page_content", "")
                    if content:
                        # Mostrar primeros 200 caracteres
                        preview = content[:200] + "..." if len(content) > 200 else content
                        pass
                    
                    # Mostrar otras claves importantes
                    for key in ["source", "category", "entity_type", "topic"]:
                        if key in point.payload:
                            pass
        except Exception as e:
            pass
        
        pass
        # Hacer una consulta de prueba
        consultas = [
            "¿Qué información hay sobre clientes?",
            "¿Qué dice sobre la máquina CNC?",
            "Información sobre mantenimiento"
        ]
        
        for consulta in consultas:
            pass
            try:
                resultado = get_rag_context(consulta, limit=2)
                if resultado and "No se encontró" not in resultado:
                    pass
                else:
                    pass
            except Exception as e:
                pass
        
        pass
        pass
        pass
        
    except Exception as e:
        pass
        pass


if __name__ == "__main__":
    inspeccionar_qdrant()