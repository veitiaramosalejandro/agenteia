import unittest

from app.agent.identity import AgentIdentityService


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl


class TestAgentIdentityService(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.service = AgentIdentityService(
            redis_client=self.redis,
            state_ttl_seconds=600,
        )

    def test_immutable_core_is_returned_as_a_copy(self):
        core = self.service.immutable_core
        core["values"].append("valor inyectado")

        self.assertNotIn("valor inyectado", self.service.immutable_core["values"])

    def test_explicit_name_choice_evolves_identity(self):
        snapshot = self.service.observe_user_message(
            session_id="session-1",
            user_id="user-1",
            user_text="Prefiero llamarte Alex",
        )

        self.assertEqual(snapshot["identity"]["name"], "Alex")
        self.assertEqual(self.service.get_identity()["name"], "Alex")

    def test_memories_are_isolated_per_user(self):
        self.service.remember_turn(
            session_id="session-1",
            user_id="user-1",
            user_text="Me llamo Ana",
            agent_response="Encantado, Ana",
        )
        self.service.remember_turn(
            session_id="session-2",
            user_id="user-2",
            user_text="Me llamo Luis",
            agent_response="Encantado, Luis",
        )

        ana_memory = self.service.get_user_memory("user-1")
        luis_memory = self.service.get_user_memory("user-2")
        self.assertEqual(len(ana_memory), 1)
        self.assertIn("Ana", ana_memory[0]["user_said"])
        self.assertNotIn("Luis", str(ana_memory))
        self.assertIn("Luis", luis_memory[0]["user_said"])
        self.assertEqual(
            self.service.get_user_profile("user-1")["relationship"]["turn_count"],
            1,
        )

    def test_style_interests_and_preferences_evolve_only_explicitly(self):
        self.service.observe_user_message(
            session_id="session-1",
            user_id="user-1",
            user_text="Quiero que tu estilo sea cálido y conciso",
        )
        self.service.observe_user_message(
            session_id="session-1",
            user_id="user-1",
            user_text="Añade la accesibilidad a tus intereses",
        )
        self.service.observe_user_message(
            session_id="session-1",
            user_id="user-1",
            user_text="Añade verificar antes de afirmar a tus preferencias",
        )

        identity = self.service.get_identity()
        self.assertEqual(identity["style"], "cálido y conciso")
        self.assertIn("la accesibilidad", identity["interests"])
        self.assertIn("verificar antes de afirmar", identity["preferences"])

    def test_temporal_state_uses_ttl_and_is_marked_as_simulated(self):
        snapshot = self.service.observe_user_message(
            session_id="urgent-session",
            user_id="user-1",
            user_text="Es urgente, tengo un fallo",
        )

        state_key = self.service._state_key("urgent-session")
        self.assertEqual(self.redis.ttls[state_key], 600)
        self.assertEqual(
            snapshot["temporal_state"]["simulated_mood"],
            "alerta y concentrado",
        )
        prompt = self.service.build_prompt_context(snapshot)
        self.assertIn("estado es una señal operativa simulada", prompt)
        self.assertIn("No afirmar conciencia", prompt)

    def test_authenticated_conversation_identity_is_persisted_and_prioritized(self):
        authenticated = {
            "resource_id": "resource-guid",
            "login_id": "login-guid",
            "full_name": "Alejandro Veitia",
            "workroom_id": "room-guid",
            "workroom_name": "Testes",
        }
        snapshot = self.service.observe_user_message(
            session_id="resource-guid",
            user_id="resource-guid",
            user_text="Mi recurso no es Dev17",
            conversation_identity=authenticated,
        )

        self.assertEqual(
            self.service.get_temporal_state("resource-guid")["conversation_identity"],
            authenticated,
        )
        prompt = self.service.build_prompt_context(snapshot)
        self.assertIn("IDResource: resource-guid", prompt)
        self.assertIn("IDLogin: login-guid", prompt)
        self.assertIn("prevalecen sobre el historial", prompt)


if __name__ == "__main__":
    unittest.main()
