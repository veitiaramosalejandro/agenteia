"""
ingest.py - Script de ingesta rápida desde agent-service/
Ejecutar: python ingest.py
"""

import os
import sys

# Agregar el directorio actual al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.rag.document_ingest import ingest_knowledge_base


if __name__ == "__main__":
    pass
    pass
    pass
    
    # Usar ruta absoluta para knowledge_base
    knowledge_dir = os.path.join(os.path.dirname(__file__), "knowledge_base")
    
    pass
    
    if not os.path.exists(knowledge_dir):
        pass
        pass
        os.makedirs(knowledge_dir, exist_ok=True)
        pass
        pass
        pass
    else:
        ingest_knowledge_base(knowledge_dir)