import os
import glob
import hashlib
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from langchain_ollama import OllamaEmbeddings
from app.config import settings
from app.rag.audio_processor import extract_audio_features

def ingestar_audios_a_qdrant(audio_dir: str = "/app/audio"):
    """Indexa patrones de audio .pcm en Qdrant, evitando duplicados por contenido."""
    client = QdrantClient(url=settings.VECTOR_DB_URL)
    embeddings = OllamaEmbeddings(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.EMBEDDING_MODEL_NAME
    )
    
    pcm_files = glob.glob(os.path.join(audio_dir, "*.pcm"))
    if not pcm_files:
        print("No se encontraron archivos .pcm en la ruta especificada.")
        return

    # Verificar si la colección existe
    collections = [c.name for c in client.get_collections().collections]
    if settings.VECTOR_COLLECTION_NAME not in collections:
        client.create_collection(
            collection_name=settings.VECTOR_COLLECTION_NAME,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE)
        )

    points = []
    nuevos = 0
    duplicados = 0
    
    for file_path in pcm_files:
        features = extract_audio_features(file_path)
        
        # 🚨 MEJORA: ID basado en hash del contenido del audio (no del nombre)
        # Leer el archivo para calcular hash
        with open(file_path, 'rb') as f:
            audio_hash = hashlib.md5(f.read()).hexdigest()
        
        point_id = f"audio_{audio_hash}"
        
        # Verificar si ya existe en Qdrant
        try:
            existing = client.retrieve(
                collection_name=settings.VECTOR_COLLECTION_NAME,
                ids=[point_id]
            )
            if existing:
                duplicados += 1
                continue  # Saltar este archivo
        except:
            pass  # Si no existe, continuar
        
        vector = embeddings.embed_query(features["text_summary"])
        
        point = PointStruct(
            id=point_id,
            vector=vector,
            payload={
                "page_content": features["text_summary"],
                "file_name": features["file_name"],
                "rms_energy": features["rms_energy"],
                "spectral_centroid": features["spectral_centroid"],
                "source": "hartford_pcm_audio",
                "file_hash": audio_hash
            }
        )
        points.append(point)
        nuevos += 1

    if points:
        client.upsert(
            collection_name=settings.VECTOR_COLLECTION_NAME,
            points=points
        )
        print(f"✅ Éxito: {nuevos} nuevos patrones de audio indexados. {duplicados} duplicados omitidos.")
    else:
        print(f"ℹ️ No hay nuevos archivos para indexar. {duplicados} duplicados omitidos.")