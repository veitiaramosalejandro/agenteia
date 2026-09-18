import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.system.learning import SistemaAprendizaje


class TestSistemaAprendizaje(unittest.TestCase):
    @patch("app.system.learning.background_checkpoint")
    @patch("app.system.learning.redis_client")
    @patch("app.system.learning.QdrantClient")
    @patch("app.system.learning.OllamaEmbeddings")
    @patch.object(SistemaAprendizaje, "_ensure_collection")
    def test_constructor_does_not_wait_for_qdrant(
        self, ensure_collection, _embeddings, _qdrant, _redis, _checkpoint
    ):
        sistema = SistemaAprendizaje()

        ensure_collection.assert_not_called()
        self.assertFalse(sistema._collection_ready)

    def test_private_knowledge_search_isolated_from_chat_interactions(self):
        sistema = SistemaAprendizaje.__new__(SistemaAprendizaje)
        sistema._embed_query_safe = lambda *_args, **_kwargs: [0.1, 0.2]
        captured = {}

        def search(_vector, query_filter=None, limit=10):
            captured["filter"] = query_filter
            return [{
                "score": 0.8,
                "payload": {
                    "scope": "agent",
                    "source": "chat-question:user-assertion:v2",
                    "canal_id": "room-1",
                    "page_content": "Hecho privado verificado.",
                },
            }]

        sistema._search_aprendizaje = search
        result = sistema.consultar_conocimiento_agente(
            "pregunta",
            agent_resource_id="resource-1",
            canal_id="room-1",
            min_score=0.6,
        )

        self.assertEqual(
            {"agent_resource_id": "resource-1", "scope": "agent"},
            captured["filter"],
        )
        self.assertEqual("Hecho privado verificado.", result)

    def test_consultar_aprendizaje_combines_channel_and_general_results(self):
        sistema = SistemaAprendizaje.__new__(SistemaAprendizaje)
        sistema.embeddings = SimpleNamespace(embed_query=lambda query: [0.1, 0.2, 0.3])
        sistema.collection = "test_collection"
        sistema.qdrant = SimpleNamespace()

        channel_hit = SimpleNamespace(id="hit_channel", payload={"page_content": "Información del canal compartida", "id": "hit_channel"})
        general_hit = SimpleNamespace(id="hit_general", payload={"page_content": "Información general aprendida", "id": "hit_general"})

        def mock_search(collection_name, query_vector, limit, query_filter=None):
            if query_filter:
                return [channel_hit]
            return [general_hit]

        sistema.qdrant.search = mock_search

        response = sistema.consultar_aprendizaje("¿Qué sabes?", canal_id="CANAL123", limit=2)

        self.assertIn("Información del canal compartida", response)
        self.assertIn("Información general aprendida", response)

    def test_consultar_aprendizaje_uses_general_results_when_channel_has_none(self):
        sistema = SistemaAprendizaje.__new__(SistemaAprendizaje)
        sistema.embeddings = SimpleNamespace(embed_query=lambda query: [0.1, 0.2, 0.3])
        sistema.collection = "test_collection"
        sistema.qdrant = SimpleNamespace()

        general_hit = SimpleNamespace(id="hit_general", payload={"page_content": "Información general disponible", "id": "hit_general"})

        def mock_search(collection_name, query_vector, limit, query_filter=None):
            if query_filter:
                return []
            return [general_hit]

        sistema.qdrant.search = mock_search

        response = sistema.consultar_aprendizaje("¿Qué sabes?", canal_id="CANAL123", limit=1)

        self.assertIn("Información general disponible", response)
        self.assertNotIn("No hay conocimiento previo", response)


if __name__ == "__main__":
    unittest.main()
