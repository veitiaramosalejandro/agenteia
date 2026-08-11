"""Configuración compartida de dimensiones y colecciones vectoriales."""

from typing import Any

from qdrant_client.models import Distance, VectorParams

from app.config import settings


def embedding_dimension(embeddings: Any) -> int:
    """Usa la dimensión configurada o la detecta consultando el modelo activo."""
    configured = int(getattr(settings, "EMBEDDING_VECTOR_SIZE", 0) or 0)
    if configured > 0:
        return configured
    return len(embeddings.embed_query("dimension probe"))


def ensure_vector_collection(client: Any, collection_name: str, embeddings: Any) -> int:
    """Crea la colección con la dimensión real y rechaza incompatibilidades visibles."""
    expected_size = embedding_dimension(embeddings)
    collections = {item.name for item in client.get_collections().collections}
    if collection_name not in collections:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=expected_size, distance=Distance.COSINE),
        )
        print(f"✅ Colección vectorial creada: {collection_name} (dim={expected_size})")
        return expected_size

    info = client.get_collection(collection_name)
    vectors = info.config.params.vectors
    current_size = getattr(vectors, "size", None)
    if current_size is not None and int(current_size) != expected_size:
        raise RuntimeError(
            f"La colección '{collection_name}' usa dim={current_size}, pero el modelo "
            f"'{settings.EMBEDDING_MODEL_NAME}' genera dim={expected_size}. "
            "Usa una colección nueva o reindexa los datos; no se pueden mezclar dimensiones."
        )
    return expected_size
