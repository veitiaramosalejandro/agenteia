import unittest

from app.agent.core import MachiningAgent


class _LearningStub:
    def __init__(self, resolved=None):
        self.resolved = resolved
        self.names = []

    def obtener_recurso_id_por_nombre(self, name):
        self.names.append(name)
        return self.resolved

    def buscar_recursos_por_nombre(self, name, limit=10):
        self.names.append(name)
        return list(self.resolved or []) if isinstance(self.resolved, list) else []


class TaskSubjectIsolationTests(unittest.TestCase):
    def setUp(self):
        self.agent = MachiningAgent.__new__(MachiningAgent)
        self.agent.sistema_aprendizaje = _LearningStub()

    def test_second_person_targets_recipient_agent_not_requester(self):
        resource, error = self.agent._business_subject_resource(
            "¿Cuál fue tu última tarea acá en el sistema?",
            requester_resource_id="alejandro-resource",
            agent_resource_id="victor-resource",
        )
        self.assertEqual("victor-resource", resource)
        self.assertIsNone(error)
        self.assertNotEqual("alejandro-resource", resource)

    def test_first_person_targets_authenticated_requester(self):
        resource, error = self.agent._business_subject_resource(
            "¿Cuál es mi tarea actual?",
            requester_resource_id="alejandro-resource",
            agent_resource_id="victor-resource",
        )
        self.assertEqual("alejandro-resource", resource)
        self.assertIsNone(error)

    def test_named_person_must_resolve_to_verified_resource(self):
        self.agent.sistema_aprendizaje = _LearningStub("paulo-resource")
        resource, error = self.agent._business_subject_resource(
            "¿Cuál es la última tarea de Paulo Ferreira en el sistema?",
            requester_resource_id="alejandro-resource",
            agent_resource_id="victor-resource",
        )
        self.assertEqual("paulo-resource", resource)
        self.assertIsNone(error)
        self.assertEqual(["Paulo Ferreira"], self.agent.sistema_aprendizaje.names)

    def test_ambiguous_subject_fails_closed(self):
        resource, error = self.agent._business_subject_resource(
            "¿Cuál es la última tarea del sistema?",
            requester_resource_id="alejandro-resource",
            agent_resource_id="victor-resource",
        )
        self.assertIsNone(resource)
        self.assertIn("No está claro", error)

    def test_mixed_first_and_second_person_fails_closed(self):
        resource, error = self.agent._business_subject_resource(
            "Compara mi tarea actual con tu tarea actual",
            requester_resource_id="alejandro-resource",
            agent_resource_id="victor-resource",
        )
        self.assertIsNone(resource)
        self.assertTrue(error)
        self.assertTrue("mezcla" in error or "mistura" in error)

    def test_second_person_is_consistent_across_business_entities(self):
        for question in (
            "¿Cuál fue tu último mensaje?",
            "¿Cuál es tu actividad actual?",
            "¿Cuáles son tus canales?",
        ):
            with self.subTest(question=question):
                resource, error = self.agent._business_subject_resource(
                    question,
                    requester_resource_id="alejandro-resource",
                    agent_resource_id="victor-resource",
                )
                self.assertEqual("victor-resource", resource)
                self.assertIsNone(error)

    def test_internal_person_lookup_lists_ambiguous_resources(self):
        self.agent.sistema_aprendizaje = _LearningStub([
            {"DisplayName": "Paulo Ferreira", "Username": "paulo.ferreira"},
            {"DisplayName": "Paulo Mateus", "Username": "Paulo.Mateus"},
        ])
        response = self.agent._resolve_internal_person_information("¿Información de Paulo?")
        self.assertIn("Paulo Ferreira", response)
        self.assertIn("Paulo Mateus", response)
        self.assertIn("varios recursos internos", response)


if __name__ == "__main__":
    unittest.main()
