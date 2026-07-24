#!/usr/bin/env python
import os
import sys
from pathlib import Path

# Agregar el directorio actual al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.rag.document_ingest import DocumentIngestor, ingest_knowledge_base
from app.agent.training import ConversationTrainer, train_from_conversations
from app.rag.sql_ingest import ingest_sql_knowledge


def train_all():
    """
    Ejecuta todos los métodos de entrenamiento.
    """
    print("\n" + "="*60)
    print("🧠 ENTRENANDO AGENTE CON CONOCIMIENTO ESPECÍFICO")
    print("="*60 + "\n")
    
    # 1. Ingesta de documentos técnicos
    print("\n📚 1. INGESTANDO DOCUMENTOS TÉCNICOS...")
    knowledge_dir = os.path.join(os.path.dirname(__file__), "knowledge_base")
    if os.path.exists(knowledge_dir):
        ingest_knowledge_base(knowledge_dir)
    else:
        print(f"⚠️ Directorio no encontrado: {knowledge_dir}")
        print("   Crea 'knowledge_base/' con subcarpetas: manuales/, guias/, procedimientos/, datos/")
    
    # 2. Entrenamiento desde conversaciones
    print("\n💬 2. APRENDIENDO DE CONVERSACIONES...")
    conv_file = os.path.join(os.path.dirname(__file__), "training_data", "conversations.json")
    if os.path.exists(conv_file):
        train_from_conversations(conv_file)
    else:
        print(f"⚠️ Archivo no encontrado: {conv_file}")
        print("   Crea 'training_data/conversations.json' con conversaciones previas")
    
    # 3. Ingesta desde SQL
    print("\n🗄️ 3. INGESTANDO DATOS DESDE SQL...")
    try:
        ingest_sql_knowledge()
    except Exception as e:
        print(f"⚠️ Error en ingesta SQL: {e}")
        print("   Verifica la conexión a SQL Server")
    
    print("\n" + "="*60)
    print("✅ ¡ENTRENAMIENTO COMPLETADO!")
    print("   El agente ahora tiene nuevo conocimiento disponible")
    print("="*60)


if __name__ == "__main__":
    train_all()