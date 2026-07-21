import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    
    # Ollama Local Configuration
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://ollama-llm:11434")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "llama3.2:3b")
    EMBEDDING_MODEL_NAME: str = os.getenv("EMBEDDING_MODEL_NAME", "nomic-embed-text")
    
    # Vector Database (Qdrant)
    VECTOR_DB_URL: str = os.getenv("VECTOR_DB_URL", "http://vector-db:6333")
    VECTOR_COLLECTION_NAME: str = "machining_docs"

    # Redis Cache / Memory
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis-cache:6379")

settings = Settings()