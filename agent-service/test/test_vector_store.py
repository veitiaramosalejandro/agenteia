import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.rag.vector_store import ensure_vector_collection


class FakeClient:
    def __init__(self, size=None):
        self.size = size
        self.created = None

    def get_collections(self):
        names = [] if self.size is None else [SimpleNamespace(name="knowledge")]
        return SimpleNamespace(collections=names)

    def create_collection(self, collection_name, vectors_config):
        self.created = (collection_name, vectors_config.size)
        self.size = vectors_config.size

    def get_collection(self, _collection_name):
        vectors = SimpleNamespace(size=self.size)
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=vectors))
        )


class VectorStoreConfigurationTests(unittest.TestCase):
    @patch("app.rag.vector_store.settings.EMBEDDING_VECTOR_SIZE", 4096)
    def test_creates_collection_with_configured_model_dimension(self):
        client = FakeClient()
        dimension = ensure_vector_collection(client, "knowledge", object())

        self.assertEqual(dimension, 4096)
        self.assertEqual(client.created, ("knowledge", 4096))

    @patch("app.rag.vector_store.settings.EMBEDDING_VECTOR_SIZE", 4096)
    def test_rejects_existing_collection_with_incompatible_dimension(self):
        client = FakeClient(size=768)

        with self.assertRaisesRegex(RuntimeError, "dim=768"):
            ensure_vector_collection(client, "knowledge", object())


if __name__ == "__main__":
    unittest.main()
