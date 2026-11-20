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
    pass
    pass
    pass
    
    # 0. Ingesta de API SOLIDSET (coleccion Postman)
    pass
    candidate_paths = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "doctus-integracion.json")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "doctus-integracion.json")),
    ]
    api_collection_file = next((p for p in candidate_paths if os.path.exists(p)), "")
    if api_collection_file:
        try:
            api_result = ingest_solidset_api_collection(api_collection_file)
            pass
        except Exception as e:
            pass
    else:
        pass

    # 1. Ingesta de documentos técnicos
    pass
    knowledge_dir = os.path.join(os.path.dirname(__file__), "knowledge_base")
    if os.path.exists(knowledge_dir):
        ingest_knowledge_base(knowledge_dir)
    else:
        pass
        pass
    
    # 2. Entrenamiento desde conversaciones
    pass
    conv_file = os.path.join(os.path.dirname(__file__), "training_data", "conversations.json")
    if os.path.exists(conv_file) and train_from_conversations is not None:
        train_from_conversations(conv_file)
    elif os.path.exists(conv_file) and train_from_conversations is None:
        pass
    else:
        pass
        pass
    
    # 3. Ingesta desde SQL
    pass
    try:
        if ingest_sql_knowledge is None:
            pass
        else:
            ingest_sql_knowledge()
    except Exception as e:
        pass
        pass
    
    pass
    pass
    pass
    pass


if __name__ == "__main__":
    train_all()