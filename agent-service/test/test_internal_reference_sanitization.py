import unittest

from app.agent.orchestrator import SolidSETOrchestrator


class InternalReferenceSanitizationTests(unittest.TestCase):
    def test_removes_mixed_language_vector_knowledge_reference(self):
        response = (
            "Soy, según la información reciente recuperada desde mi vectorial "
            "knowledge base, una identidad seleccionada."
        )

        result = SolidSETOrchestrator._hide_internal_implementation_details(response, "es")

        self.assertNotIn("knowledge base", result.lower())
        self.assertIn("la información disponible", result)
        self.assertNotIn("la la información", result.lower())

    def test_removes_rag_and_qdrant_names(self):
        response = "Consultei o RAG e o Qdrant para responder."

        result = SolidSETOrchestrator._hide_internal_implementation_details(response, "pt")

        self.assertNotIn("rag", result.lower())
        self.assertNotIn("qdrant", result.lower())


if __name__ == "__main__":
    unittest.main()
