import os
import glob
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from langchain_ollama import OllamaEmbeddings
from app.config import settings
from app.rag.audio_processor import extract_audio_features

def ingestar_audios_a_qdrant(audio_dir: str = "/app/audio"):
    """Indexa todos los patrones de audio .pcm de la máquina HARTFORD en Qdrant."""
    client = QdrantClient(url=settings.VECTOR_DB_URL)
    embeddings = OllamaEmbeddings(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.EMBEDDING_MODEL_NAME
    )
    
    pcm_files = glob.glob(os.path.join(audio_dir, "*.pcm"))
    if not pcm_files:
        print("No se encontraron archivos .pcm en la ruta especificada.")
        return

    # Asegurar que la colección existe
    collections = [c.name for c in client.get_collections().collections]
    if settings.VECTOR_COLLECTION_NAME not in collections:
        client.create_collection(
            collection_name=settings.VECTOR_COLLECTION_NAME,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE) # O la dimensión de tu embedding
        )

    points = []
    for file_path in pcm_files:
        features = extract_audio_features(file_path)
        
        # Generar embedding del texto descriptivo de las frecuencias
        vector = embeddings.embed_query(features["text_summary"])

        # Usar un id estable por nombre de archivo para evitar duplicados en reingestas.
        point_id = os.path.basename(file_path)
        point = PointStruct(
            id=point_id,
            vector=vector,
            payload={
                "page_content": features["text_summary"],
                "file_name": features["file_name"],
                "rms_energy": features["rms_energy"],
                "spectral_centroid": features["spectral_centroid"],
                "source": "hartford_pcm_audio"
            }
        )
        points.append(point)

    client.upsert(
        collection_name=settings.VECTOR_COLLECTION_NAME,
        points=points
    )
    print(f"Éxito: Se indexaron {len(points)} patrones de audio .pcm en Qdrant.")