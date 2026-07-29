import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

class Settings(BaseSettings):
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    
    # Ollama Local Configuration
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://ollama-llm:11435")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "qwen2.5:7b")
    EMBEDDING_MODEL_NAME: str = os.getenv("EMBEDDING_MODEL_NAME", "nomic-embed-text")
    
    # Vector Database (Qdrant)
    VECTOR_DB_URL: str = os.getenv("VECTOR_DB_URL", "http://vector-db:6333")
    VECTOR_COLLECTION_NAME: str = os.getenv("VECTOR_COLLECTION_NAME", "machining_docs")

    # Redis Cache / Memory
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis-cache:6379")
    
    # PostgreSQL / TimescaleDB
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "timescaledb")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "user")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "pass")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "machining_db")
    
    # SQL Server (para compatibilidad)
    SQL_SERVER_HOST: str = os.getenv("SQL_SERVER_HOST", "172.16.10.167")
    SQL_SERVER_USER: str = os.getenv("SQL_SERVER_USER", "sa")
    SQL_SERVER_PASSWORD: str = os.getenv("SQL_SERVER_PASSWORD", "Abcd*1234")
    SQL_SERVER_DB: str = os.getenv("SQL_SERVER_DB", "ISIFrameIsicom")
    DB_STUDY_INTERVAL_SECONDS: int = int(os.getenv("DB_STUDY_INTERVAL_SECONDS", "3600"))
    DB_STUDY_IDLE_CHECK_SECONDS: int = int(os.getenv("DB_STUDY_IDLE_CHECK_SECONDS", "10"))
    DB_STUDY_MAX_RUN_SECONDS: int = int(os.getenv("DB_STUDY_MAX_RUN_SECONDS", "900"))
    
    # Audio
    AUDIO_DIR: str = os.getenv("AUDIO_DIR", "./audio_samples")
    INGEST_INTERVAL_SECONDS: int = int(os.getenv("INGEST_INTERVAL_SECONDS", "3600"))
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()

# Imprimir configuración para debug
if settings.ENVIRONMENT == "development":
    print("🔧 Configuración de desarrollo:")
    print(f"  - Ollama: {settings.OLLAMA_BASE_URL}")
    print(f"  - Qdrant: {settings.VECTOR_DB_URL}")
    print(f"  - Redis: {settings.REDIS_URL}")
    print(f"  - PostgreSQL: {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}")