import unittest

from app.agent.language import LanguageResolver


class FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, _ttl, value):
        self.values[key] = value


class TestLanguageResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = LanguageResolver()
        self.resolver._redis = FakeRedis()

    def test_detects_languages_without_phrase_markers(self):
        samples = {
            "pt": "Gostaria de conhecer melhor a história desta empresa industrial.",
            "es": "Quisiera conocer mejor la historia de esta empresa industrial.",
            "en": "I would like to learn more about the history of this company.",
            "fr": "Je voudrais mieux connaître l'histoire de cette entreprise.",
            "de": "Ich möchte mehr über die Geschichte dieses Unternehmens erfahren.",
        }
        for expected, text in samples.items():
            with self.subTest(language=expected):
                decision = self.resolver.resolve(text, session_id=f"session-{expected}")
                self.assertEqual(expected, decision.language)
                self.assertEqual("statistical_ensemble", decision.source)

    def test_ambiguous_name_uses_conversation_language(self):
        self.resolver.resolve(
            "Gostaria de conhecer melhor esta organização.",
            session_id="conversation-1",
        )
        decision = self.resolver.resolve(
            "Robotea",
            session_id="conversation-1",
            locale="es-ES",
        )

        self.assertEqual("pt", decision.language)
        self.assertEqual("conversation", decision.source)

    def test_locale_is_used_when_session_has_no_language(self):
        decision = self.resolver.resolve(
            "Robotea", session_id="new-session", locale="pt-PT"
        )

        self.assertEqual("pt", decision.language)
        self.assertEqual("locale", decision.source)

    def test_opening_question_mark_is_unambiguous_spanish_signal(self):
        decision = self.resolver.resolve(
            "¿En qué canales participas?", session_id="short-spanish", locale="pt-PT"
        )
        self.assertEqual("es", decision.language)
        self.assertEqual("orthographic_signal", decision.source)


if __name__ == "__main__":
    unittest.main()
