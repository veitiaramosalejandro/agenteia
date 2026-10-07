#!/usr/bin/env python
import os
import sys

# Agregar el directorio actual al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.rag.document_ingest import ingest_knowledge_base
from app.rag.solidset_api_ingest import ingest_solidset_api_collection

try:
    from app.agent.training import train_from_conversations
except Exception:
    train_from_conversations = None

try:
    from app.rag.sql_ingest import ingest_sql_knowledge
except Exception:
    ingest_sql_knowledge = None


def train_all():
    """
    Ejecuta todos los métodos de entrenamiento.
    """
    print("\n" + "="*60)
    print("🧠 ENTRENANDO AGENTE CON CONOCIMIENTO ESPECÍFICO")
    print("="*60 + "\n")
    
    # 0. Ingesta de API SOLIDSET (coleccion Postman)
    print("\n🧩 0. ENTRENANDO API SOLIDSET DESDE COLECCION...")
    candidate_paths = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "doctus-integracion.json")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "doctus-integracion.json")),
    ]
    api_collection_file = next((p for p in candidate_paths if os.path.exists(p)), "")
    if api_collection_file:
        try:
            api_result = ingest_solidset_api_collection(api_collection_file)
            print(
                "   ✅ API SOLIDSET indexada: "
                f"endpoints={api_result.get('endpoints_found', 0)}, "
                f"upserted={api_result.get('points_upserted', 0)}"
            )
        except Exception as e:
            print(f"⚠️ Error entrenando API SOLIDSET: {e}")
    else:
        print("⚠️ Archivo no encontrado: doctus-integracion.json (ni en agent-service ni en raíz del workspace)")

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
    if os.path.exists(conv_file) and train_from_conversations is not None:
        train_from_conversations(conv_file)
    elif os.path.exists(conv_file) and train_from_conversations is None:
        print("⚠️ Módulo app.agent.training no disponible; se omite entrenamiento conversacional")
    else:
        print(f"⚠️ Archivo no encontrado: {conv_file}")
        print("   Crea 'training_data/conversations.json' con conversaciones previas")
    
    # 3. Ingesta desde SQL
    print("\n🗄️ 3. INGESTANDO DATOS DESDE SQL...")
    try:
        if ingest_sql_knowledge is None:
            print("⚠️ Módulo app.rag.sql_ingest no disponible; se omite ingesta SQL legacy")
        else:
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