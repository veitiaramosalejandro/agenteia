import unittest

from app.system.learning import SistemaAprendizaje


class TestOpenAILocalLearning(unittest.TestCase):
    @staticmethod
    def _service_with_hits(hits):
        service = SistemaAprendizaje.__new__(SistemaAprendizaje)
        service._embed_query_safe = lambda query, context: [0.1, 0.2]
        service._search_aprendizaje = lambda vector, query_filter, limit: hits
        return service

    def test_reuses_answer_for_same_normalized_question(self):
        service = self._service_with_hits([
            {
                "score": 0.95,
                "payload": {
                    "metadatos": {
                        "question": "  PODERIA dar-me uma comparação   entre REST e GraphQL? ",
                        "answer": "Resposta em cache.",
                    }
                },
            }
        ])

        result = service.consultar_respuesta_openai(
            "Poderia dar-me uma comparação entre REST e GraphQL?",
            agent_resource_id="agent-1",
            min_score=0.7,
        )

        self.assertEqual(result, "Resposta em cache.")

    def test_rejects_semantically_related_but_different_question(self):
        service = self._service_with_hits([
            {
                "score": 0.99,
                "payload": {
                    "metadatos": {
                        "question": "Como criar uma API GraphQL?",
                        "answer": "Passos para criar uma API GraphQL.",
                    }
                },
            }
        ])

        result = service.consultar_respuesta_openai(
            "Poderia dar-me uma comparação entre REST e GraphQL?",
            agent_resource_id="agent-1",
            min_score=0.7,
        )

        self.assertEqual(result, "")

    def test_rejects_legacy_hit_without_original_question(self):
        service = self._service_with_hits([
            {
                "score": 0.99,
                "payload": {"metadatos": {"answer": "Resposta antiga."}},
            }
        ])

        result = service.consultar_respuesta_openai(
            "Pergunta atual",
            agent_resource_id="agent-1",
        )

        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
