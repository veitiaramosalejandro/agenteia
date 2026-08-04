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
    print("\n" + "="*60)
    print("📚 INGESTOR DE DOCUMENTOS")
    print("="*60 + "\n")
    
    # Usar ruta absoluta para knowledge_base
    knowledge_dir = os.path.join(os.path.dirname(__file__), "knowledge_base")
    
    print(f"📂 Buscando documentos en: {knowledge_dir}")
    
    if not os.path.exists(knowledge_dir):
        print(f"❌ Directorio no encontrado: {knowledge_dir}")
        print("   Creando directorio...")
        os.makedirs(knowledge_dir, exist_ok=True)
        print(f"✅ Directorio creado: {knowledge_dir}")
        print("   Agrega tus documentos (TXT, PDF, DOCX, MD) en esta carpeta")
        print("   Luego ejecuta nuevamente: python ingest.py")
    else:
        ingest_knowledge_base(knowledge_dir)