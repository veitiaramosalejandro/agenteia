"""Configuración compartida de dimensiones y colecciones vectoriales."""

import time
from typing import Any

from qdrant_client.models import Distance, VectorParams

from app.config import settings


def embedding_dimension(embeddings: Any) -> int:
    """Usa la dimensión configurada o la detecta consultando el modelo activo."""
    configured = int(getattr(settings, "EMBEDDING_VECTOR_SIZE", 0) or 0)
    if configured > 0:
        return configured
    return len(embeddings.embed_query("dimension probe"))


def ensure_vector_collection(
    client: Any, collection_name: str, embeddings: Any, max_retries: int = 10, retry_delay: float = 2.0
) -> int:
    """Crea la colección con la dimensión real y rechaza incompatibilidades visibles, reintentando durante el arranque."""
    last_exc = None
    for attempt in range(max_retries):
        try:
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
        except Exception as exc:
            last_exc = exc
            err_msg = str(exc).lower()
            if isinstance(exc, RuntimeError) and "no se pueden mezclar dimensiones" in err_msg:
                raise exc
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
    if last_exc:
        raise last_exc
