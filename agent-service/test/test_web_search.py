import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.tools import google_web_search
from app.agent.core import MachiningAgent


class FakeDDGS:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def text(self, query, **kwargs):
        return [
            {"title": "Manual técnico", "body": "Explicación comprobable", "href": "https://example.com/manual"},
            {"title": "Duplicado", "body": "Otra copia", "href": "https://example.com/manual"},
        ]


class TestWebSearch(unittest.TestCase):
    @patch("app.agent.tools._schedule_web_search_learning")
    def test_search_returns_sources_and_schedules_learning(self, schedule):
        fake_module = SimpleNamespace(DDGS=FakeDDGS)
        with patch.dict(sys.modules, {"ddgs": fake_module}), patch(
            "app.agent.tools.settings.EXTERNAL_SEARCH_PROVIDER", "ddgs"
        ):
            response = google_web_search.invoke({"query": "alarma CNC"})
        payload = json.loads(response)

        self.assertFalse(payload["learned"])
        self.assertTrue(payload["learning_scheduled"])
        self.assertTrue(payload["external_unverified"])
        self.assertEqual(payload["source_type"], "ddgs_web_search")
        self.assertEqual(payload["results"][0]["url"], "https://example.com/manual")
        self.assertEqual(len(payload["results"]), 1)
        schedule.assert_called_once()

    @patch("app.agent.tools._schedule_web_search_learning")
    @patch("app.services.external_search.search_with_openai")
    def test_openai_search_preserves_sources_and_schedules_learning(self, search, schedule):
        from app.services.external_search import ExternalSearchResult

        search.return_value = [ExternalSearchResult(
            title="Fuente oficial",
            snippet="Hecho actualizado con evidencia.",
            url="https://example.com/current",
        )]
        with patch("app.agent.tools.settings.EXTERNAL_SEARCH_PROVIDER", "openai"):
            response = google_web_search.invoke({"query": "dato actual"})
        payload = json.loads(response)

        self.assertEqual(payload["source_type"], "openai_web_search")
        self.assertEqual(payload["answer"], "Hecho actualizado con evidencia.")
        self.assertEqual(payload["results"][0]["title"], "Fuente oficial")
        search.assert_called_once_with("dato actual", resource_id=None)
        schedule.assert_called_once()

    def test_empty_query_is_rejected(self):
        response = google_web_search.invoke({"query": "   "})
        self.assertIn("no puede estar vacía", response)

    @patch("app.services.external_search.search_with_openai", return_value=[])
    def test_agent_identity_is_injected_by_backend_not_tool_schema(self, search):
        with patch("app.agent.tools.settings.EXTERNAL_SEARCH_PROVIDER", "openai"):
            google_web_search.invoke(
                {"query": "consulta pública"},
                config={"configurable": {"agent_resource_id": "agent-a"}},
            )
        search.assert_called_once_with("consulta pública", resource_id="agent-a")
        self.assertEqual(set(google_web_search.tool_call_schema.model_fields), {"query"})

    @patch("app.services.external_search.search_with_openai", side_effect=RuntimeError("secret-key-private"))
    def test_provider_errors_do_not_expose_credentials(self, search):
        with patch("app.agent.tools.settings.EXTERNAL_SEARCH_PROVIDER", "openai"):
            response = google_web_search.invoke({"query": "consulta pública"})
        self.assertTrue(response.startswith("Error"))
        self.assertNotIn("secret-key-private", response)


    def test_missing_knowledge_response_triggers_fallback(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertTrue(agent._response_needs_web_fallback(
            "No tengo información específica sobre el modelo Kimi-K3.", []
        ))

    def test_fallback_is_not_repeated_after_web_search(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertFalse(agent._response_needs_web_fallback(
            "No tengo información suficiente.", ["google_web_search"]
        ))

    def test_sports_schedule_is_external_information(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertTrue(agent._is_external_information_query(
            "¿Cuál es el primer partido del Real Madrid en la temporada 2026-2027?"
        ))

    def test_search_authorization_is_external_information(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertTrue(agent._is_external_information_query(
            "Sí, necesito que busques por favor"
        ))

    def test_portuguese_current_president_is_external_and_forces_fresh_search(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        question = "quem é o presidente de Portugal?"
        self.assertTrue(agent._is_external_information_query(question))
        self.assertTrue(agent._is_current_officeholder_query(question))

    def test_current_officeholder_detection_is_multilingual(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertTrue(agent._is_current_officeholder_query(
            "¿Quién es el presidente de Portugal?"
        ))
        self.assertTrue(agent._is_current_officeholder_query(
            "Who is the prime minister of Portugal?"
        ))

    def test_current_officeholder_tolerates_missing_separators_and_accents(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        variants = (
            "quem é o primeiroministro de portugal?",
            "Quem e o PRIMEIRO-MINISTRO de Portugal",
            "quien es el primerministro de portugal",
            "who is the primeminister of portugal",
        )
        for question in variants:
            with self.subTest(question=question):
                self.assertTrue(agent._is_current_officeholder_query(question))
                self.assertTrue(agent._is_external_information_query(question))

    def test_volatile_queries_always_require_fresh_search(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        queries = (
            "Dime temperatura actual de Leiria",
            "Qual é o tempo em Lisboa?",
            "Precio del bitcoin",
            "Últimas noticias de Portugal",
            "Resultado del partido del Real Madrid",
            "Estado actual del servicio",
        )
        for query in queries:
            with self.subTest(query=query):
                self.assertTrue(agent._requires_fresh_web_search(query))

    def test_stable_external_query_can_reuse_recent_knowledge(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertFalse(agent._requires_fresh_web_search(
            "Historia del lenguaje de programación Python"
        ))

    def test_unrelated_internal_question_is_not_current_officeholder(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertFalse(agent._is_current_officeholder_query(
            "Quem é o responsável desta tarefa interna?"
        ))

    def test_natural_current_date_requests_are_deterministic(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        for question in (
            "Dime el día de hoy en portugués",
            "Dime qué día es hoy en portugués",
            "Qual é a data de hoje?",
            "Tell me today's date",
        ):
            with self.subTest(question=question):
                self.assertTrue(agent._is_current_datetime_query(question))

    def test_numeric_web_claims_must_exist_in_evidence(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        evidence = '{"snippet":"Temperatura actual: 20 °C, máxima: 25 °C"}'
        self.assertTrue(agent._numeric_claims_supported(
            "La temperatura actual es de 20 °C.", evidence
        ))
        self.assertFalse(agent._numeric_claims_supported(
            "La temperatura actual es de 39 °C.", evidence
        ))

    def test_provider_summary_is_available_when_local_model_changes_a_number(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        evidence = json.dumps({
            "results": [{
                "title": "Meteorología",
                "snippet": "La temperatura actual en Leiria es 20 °C.",
                "url": "https://example.com/weather",
            }]
        })
        grounded = agent._grounded_web_answer(evidence)
        self.assertEqual(grounded, "La temperatura actual en Leiria es 20 °C.")
        self.assertTrue(agent._numeric_claims_supported(grounded, evidence))

    def test_unverified_guard_message_is_treated_as_deflection(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertTrue(agent._is_deflecting_concrete_answer(
            "No pude verificar el dato solicitado con la evidencia disponible."
        ))

    def test_domain_only_turn_is_external_and_keeps_previous_topic(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertTrue(agent._is_external_information_query("www.marca.com"))
        query = agent._contextual_web_query(
            "www.marca.com",
            [
                "primer partido del Real Madrid temporada 2026-2027",
                "Sí necesito que busques por favor",
            ],
        )
        self.assertEqual(
            query,
            "primer partido del Real Madrid temporada 2026-2027 site:www.marca.com",
        )

    def test_web_answer_hides_links_and_generic_attribution(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        answer = agent._clean_web_answer(
            "Según [este artículo](https://example.com/kimi), Kimi K3 destaca en programación."
        )
        self.assertNotIn("http", answer)
        self.assertNotIn("Según", answer)
        self.assertIn("Kimi K3 destaca en programación", answer)

    def test_web_answer_removes_source_only_lists(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        answer = agent._clean_web_answer(
            "Basado en la información obtenida, hoy habrá 24 grados.\n\n"
            "Puedes consultar más detalles en los siguientes links:\n"
            "- [Pronóstico local](https://example.com/weather)"
        )
        self.assertNotIn("http", answer)
        self.assertNotIn("Pronóstico local", answer)
        self.assertNotIn("información obtenida", answer)
        self.assertEqual(answer, "hoy habrá 24 grados.")

    def test_web_results_have_useful_fallback_without_llm(self):
        payload = json.dumps({
            "results": [{
                "title": "Plantilla Real Madrid",
                "snippet": "Listado actualizado de jugadores para la temporada.",
                "url": "https://example.com/squad",
            }]
        })
        answer = MachiningAgent._web_results_without_llm(payload, "plantilla")
        self.assertIn("Plantilla Real Madrid", answer)
        self.assertIn("Listado actualizado", answer)
        self.assertNotIn("https://", answer)

    def test_web_knowledge_is_available_immediately_after_search(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        agent.web_knowledge_cache = {}
        query = "plantilla Real Madrid temporada 2026-2027"
        agent._cache_web_knowledge(query, '{"results": [{"title": "Plantilla"}]}')
        self.assertIn("Plantilla", agent._get_cached_web_knowledge(query))


if __name__ == "__main__":
    unittest.main()
