import json
import hashlib
from typing import List, Dict, Any
from datetime import datetime

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from langchain_ollama import OllamaEmbeddings

from app.config import settings


class ConversationTrainer:
    """
    Entrena al agente a partir de conversaciones previas.
    """
    
    def __init__(self):
        self.embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL_NAME
        )
        self.qdrant = QdrantClient(url=settings.VECTOR_DB_URL)
        self.collection = settings.VECTOR_COLLECTION_NAME
    
    def train_from_conversation(self, conversation: Dict[str, Any]) -> int:
        """
        Aprende de una conversación completa.
        
        Args:
            conversation: Diccionario con la conversación
                {
                    "session_id": "xxx",
                    "user_id": "xxx",
                    "messages": [
                        {"role": "user", "content": "...", "timestamp": "..."},
                        {"role": "assistant", "content": "...", "timestamp": "..."}
                    ],
                    "metadata": {"topic": "..."}
                }
        
        Returns:
            Número de puntos indexados
        """
        messages = conversation.get("messages", [])
        if not messages:
            return 0
        
        points = []
        
        # Identificar pares pregunta-respuesta
        for i in range(len(messages) - 1):
            if messages[i]["role"] == "user" and messages[i+1]["role"] == "assistant":
                user_msg = messages[i]["content"]
                assistant_msg = messages[i+1]["content"]
                
                # Crear texto de aprendizaje
                learning_text = f"""
                PREGUNTA: {user_msg}
                RESPUESTA DEL ASISTENTE: {assistant_msg}
                
                Contexto adicional:
                - Usuario: {conversation.get('user_id', 'desconocido')}
                - Tema: {conversation.get('metadata', {}).get('topic', 'general')}
                """
                
                # Generar embedding
                vector = self.embeddings.embed_query(learning_text)
                
                # ID basado en hash
                content_hash = hashlib.md5(learning_text.encode()).hexdigest()[:16]
                point_id = f"conv_{content_hash}_{i}"
                
                point = PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "page_content": learning_text,
                        "user_question": user_msg,
                        "assistant_answer": assistant_msg,
                        "session_id": conversation.get("session_id"),
                        "user_id": conversation.get("user_id"),
                        "topic": conversation.get("metadata", {}).get("topic", "general"),
                        "source": "conversation_training",
                        "timestamp": datetime.now().isoformat()
                    }
                )
                points.append(point)
        
        if points:
            self.qdrant.upsert(
                collection_name=self.collection,
                points=points
            )
            print(f"✅ Aprendidas {len(points)} interacciones de la conversación")
            return len(points)
        
        return 0
    
    def train_from_conversation_file(self, file_path: str) -> int:
        """
        Entrena desde un archivo JSON con conversaciones.
        
        Formato esperado:
        [
            {
                "session_id": "xxx",
                "user_id": "xxx",
                "messages": [...],
                "metadata": {...}
            },
            ...
        ]
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                conversations = json.load(f)
            
            total = 0
            for conv in conversations:
                total += self.train_from_conversation(conv)
            
            print(f"✅ Total aprendido: {total} interacciones")
            return total
            
        except Exception as e:
            print(f"❌ Error entrenando desde archivo: {e}")
            return 0


def train_from_conversations(conversations_file: str):
    """
    Función de utilidad para entrenar desde un archivo de conversaciones.
    """
    trainer = ConversationTrainer()
    trainer.train_from_conversation_file(conversations_file)