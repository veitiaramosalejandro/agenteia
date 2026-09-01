import unittest
from unittest.mock import patch

from app.main import (
    app,
    _attach_solidset_instance,
    _chat_question_suggestion_context,
    _format_suggestion_scope_context,
    _format_related_records_context,
    _related_record_direct_answer,
    _chat_question_session_id,
    _suggestion_count,
    _safe_chat_question_fallback,
    _suggestion_title,
    _suggestion_tool_allowlist,
    _verified_suggestion_business_context,
    _suggestion_request_text,
    _local_arithmetic_response,
    _local_temporal_response,
    _parse_chat_question_suggestions,
    _suggestion_language_is_consistent,
    _is_business_recommendation_request,
    _is_concrete_suggestion_answer_request,
    _is_research_suggestion_request,
    _is_related_record_guidance_request,
    _suggestion_request_language,
    _suggestion_matches_related_records,
    _related_guidance_is_useful,
    _related_guidance_fallback,
    _reason_about_related_record,
    _sanitize_related_record_value,
    _extract_learnable_suggestion_fact,
    _is_relative_temporal_assertion,
    _is_safe_auto_reply_output,
    _learn_direct_agent_assertion,
)
from app.agent.orchestrator import SolidSETOrchestrator
from app.agent.core import MachiningAgent
from app.knowledge_provenance import (
    LEGACY_SUGGESTION_SOURCE,
    USER_ASSERTION_SOURCE,
    usable_agent_knowledge,
)


class TestChatQuestionSuggestion(unittest.TestCase):
    def test_pure_arithmetic_is_resolved_locally_and_safely(self):
        self.assertEqual("8*8 = **64**.", _local_arithmetic_response("8*8 ?"))
        self.assertEqual("(12+4)/2 = **8**.", _local_arithmetic_response("(12+4)/2"))
        self.assertIsNone(_local_arithmetic_response("__import__('os')"))
        self.assertIsNone(_local_arithmetic_response("8/0"))

    def test_internal_tool_payload_is_not_safe_user_output(self):
        leaked = '{"tool": "query_sql_server", "arguments": {"sql_query": "SELECT * FROM Tareas"}}'
        self.assertFalse(_is_safe_auto_reply_output(leaked))

    def test_relative_date_assertion_is_not_durable_knowledge(self):
        self.assertTrue(_is_relative_temporal_assertion("Hoy es 28 de agosto."))
        self.assertTrue(_is_relative_temporal_assertion("Hoje é 28 de agosto."))
        self.assertFalse(_is_relative_temporal_assertion("La máquina usa aceite ISO 46."))

    @patch("app.main.agent.sistema_aprendizaje.aprender_conocimiento_agente", return_value=True)
    @patch("app.main.save_agent_knowledge")
    def test_direct_assertion_is_persisted_for_selected_agent(self, save, index):
        save.return_value = {"ID": "knowledge-1", "WasExisting": False}

        learned = _learn_direct_agent_assertion({
            "message": "La máquina usa aceite ISO 46.",
            "agent_resource_id": "agent-1",
            "channel_id": "room-1",
        })

        self.assertTrue(learned)
        payload = save.call_args.args[0]
        self.assertEqual(payload["IDResource"], "agent-1")
        self.assertEqual(payload["IDWorkRoom"], "room-1")
        self.assertEqual(payload["Source"], USER_ASSERTION_SOURCE)
        index.assert_called_once_with(save.return_value)

    def test_swagger_contains_fictitious_quoted_message_example(self):
        operation = app.openapi()["paths"][
            "/api/v1/agent/notification/chat-question/suggest-response"
        ]["post"]
        examples = operation["requestBody"]["content"]["application/json"]["examples"]
        payload = examples["quotedMeetingMessage"]["value"]

        self.assertEqual("", payload["Chat"]["rawMessage"])
        self.assertEqual(990002, payload["Chat"]["idChat2"])
        self.assertEqual(990001, payload["Chat"]["chatQuestion"]["idChat2"])
        self.assertTrue(payload["Chat"]["chatQuestion"]["rawMessage"])
        self.assertNotEqual(
            payload["Chat"]["idSenderResource"],
            payload["Chat"]["chatQuestion"]["idSenderResource"],
        )

    def test_swagger_contains_empty_channel_context_example(self):
        operation = app.openapi()["paths"][
            "/api/v1/agent/notification/chat-question/suggest-response"
        ]["post"]
        examples = operation["requestBody"]["content"]["application/json"]["examples"]
        payload = examples["emptyContextAdvice"]["value"]

        # FastAPI omits null fields while encoding OpenAPI examples; omission is
        # equivalent to Chat=null for this optional FrameworkMessage field.
        self.assertIsNone(payload.get("Chat"))
        self.assertEqual("", payload["RawMessage"])
        self.assertEqual("1", payload["Info"]["advice_mode"])
        self.assertTrue(payload["Info"]["request_id"])

    def test_empty_advice_payload_uses_info_and_workroom_context(self):
        payload = {
            "Sender": {
                "session": "00000000-0000-0000-0000-000000000000",
                "login": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "resource": "11111111-1111-4111-8111-111111111111",
                "workRoom": "33333333-3333-4333-8333-333333333333",
            },
            "Destiny": {"workRoom": "33333333-3333-4333-8333-333333333333"},
            "RawMessage": "",
            "Chat": None,
            "Info": {
                "session_id": "11111111-1111-4111-8111-111111111111",
                "advice_mode": "1",
                "request_id": "66666666-6666-4666-8666-666666666666",
            },
        }

        context = _chat_question_suggestion_context(payload)

        self.assertEqual("66666666-6666-4666-8666-666666666666", context["request_id"])
        self.assertEqual("11111111-1111-4111-8111-111111111111", context["session_id"])
        self.assertEqual("33333333-3333-4333-8333-333333333333", context["workroom_id"])
        self.assertEqual("1", context["advice_mode"])
        rendered = _format_suggestion_scope_context(
            [{"message": "Tema pendiente", "sender_full_name": "Ana", "timestamp": None}]
        )
        self.assertEqual("Ana: Tema pendiente", rendered)

    def test_advice_session_is_stable_between_initial_and_continuous_payloads(self):
        common = {
            "requester_resource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
            "workroom_id": "d8e82821-d52f-44bf-9b70-682651a6196e",
        }
        initial = {**common, "request_id": "1757618085", "quoted_chat_id": ""}
        continuous = {**common, "request_id": "1757618087", "quoted_chat_id": "1757618088"}

        self.assertEqual(
            _chat_question_session_id(initial),
            _chat_question_session_id(continuous),
        )

    def test_zero_quoted_chat_id_is_a_new_advice_request_not_a_refinement(self):
        context = _chat_question_suggestion_context({
            "Sender": {
                "resource": "11111111-1111-4111-8111-111111111111",
            },
            "Chat": {
                "idChat2": 123,
                "idSenderResource": "11111111-1111-4111-8111-111111111111",
                "idWorkRoom": "33333333-3333-4333-8333-333333333333",
                "chatQuestion": {
                    "idChat2": 0,
                    "rawMessage": (
                        "Pedido do utilizador:\nQue tareas tiene asignado "
                        "el recurso Alejandro Veitia"
                    ),
                },
            },
            "Info": {"advice_mode": "1"},
        })

        self.assertEqual("", context["quoted_chat_id"])
        self.assertTrue(context["quoted_message"].startswith("Pedido do utilizador"))
        self.assertEqual(
            "Que tareas tiene asignado el recurso Alejandro Veitia",
            _suggestion_request_text(context["quoted_message"]),
        )

    def test_related_task_is_preserved_as_authoritative_turn_context(self):
        context = _chat_question_suggestion_context({
            "Sender": {"resource": "11111111-1111-4111-8111-111111111111"},
            "Chat": {
                "idChat2": 81046402,
                "idWorkRoom": "33333333-3333-4333-8333-333333333333",
                "chatQuestion": {"idChat2": 0, "rawMessage": "Que debo hacer para hacer esta tarea?"},
            },
            "Info": {"advice_mode": "1"},
            "RelatedRecordsData": [{
                "gidRecord": "60debe73-2ba2-f111-87af-ac162d7b04d3",
                "recordCode": "T-26-11369",
                "recordShortName": "Implementação de formação melhorada.",
                "recordTypeName": "Task",
            }],
        })
        self.assertEqual("T-26-11369", context["related_records"][0]["recordCode"])
        rendered = _format_related_records_context(context["related_records"])
        self.assertIn("T-26-11369", rendered)
        self.assertIn("Implementação de formação melhorada.", rendered)

    def test_related_record_answer_cannot_reuse_previous_company_topic(self):
        answer = _related_record_direct_answer(
            "Task: T-26-11369 — Implementação de formação melhorada.\n"
            "Description: Modificação para aceitação de reações com emojis.",
            "es",
        )
        self.assertIn("T-26-11369", answer)
        self.assertIn("reacciones", answer.replace("reações", "reacciones"))
        self.assertIn("Descripción verificada", answer)
        self.assertNotIn("Sugerencia de ejecución", answer)
        self.assertNotIn("**", answer)
        self.assertNotIn("ROBOTEA", answer)

    def test_stale_task_wrapper_is_removed_before_reasoning(self):
        wrapped = (
            "Tarefa: T-26-11369 Implementação de formação melhorada.\n"
            "Texto antigo sobre N8N.\n\nPedido do utilizador:\n"
            "Investiga como poder resolver esta tarea"
        )
        self.assertEqual(
            "Investiga como poder resolver esta tarea",
            _suggestion_request_text(wrapped),
        )
        self.assertTrue(_is_research_suggestion_request(_suggestion_request_text(wrapped)))

    def test_related_record_sessions_are_isolated_by_record(self):
        base = {
            "requester_resource": "resource-1",
            "workroom_id": "room-1",
        }
        first = _chat_question_session_id({
            **base, "related_records": [{"recordCode": "T-26-11369"}],
        })
        second = _chat_question_session_id({
            **base, "related_records": [{"recordCode": "T-26-11245"}],
        })
        self.assertNotEqual(first, second)

    def test_related_record_gate_rejects_another_task(self):
        records = [{
            "recordCode": "T-26-11245",
            "recordShortName": "Automação N8N integrar Sistema SolidSET",
            "recordTypeName": "Task",
        }]
        self.assertFalse(_suggestion_matches_related_records(
            "Para a tarefa T-26-11369 devemos melhorar a formação.", records
        ))
        self.assertTrue(_suggestion_matches_related_records(
            "Para T-26-11245, analisa a integração N8N com SolidSET.", records
        ))

    def test_related_record_fallback_does_not_invent_an_architecture(self):
        answer = _related_record_direct_answer(
            "Task: T-26-11245 — Automação N8N - integrar com o Sistema SolidSET\n"
            "Description: Automatizar uma BD SolidSET ou integrar com ERPs usando N8N.",
            "es",
        )
        self.assertIn("Descripción verificada", answer)
        self.assertNotIn("Criterio recomendado", answer)
        self.assertNotIn("Data API de SolidSET", answer)
        self.assertNotIn("T-26-11369", answer)

    def test_task_guidance_is_reasoning_mode_not_concrete_template(self):
        for request in (
            "Que puedo hacer en esta tarea?",
            "Que debo hacer para esta tarea?",
            "Investiga como poder resolver esta tarea",
            "Fale-me da tarefa e dê-me algumas sugestões de estudo?",
            "Porquê é que não pode realizar uma análise desta tarefa para mim?",
        ):
            with self.subTest(request=request):
                self.assertTrue(_is_related_record_guidance_request(request))
                self.assertTrue(_is_business_recommendation_request(request))

    def test_screenshot_requests_are_not_misclassified_as_factual_answers(self):
        for request in (
            "Fale-me da tarefa e dê-me algumas sugestões de estudo?",
            "Porquê é que não pode realizar uma análise desta tarefa para mim?",
        ):
            with self.subTest(request=request):
                self.assertTrue(_is_related_record_guidance_request(request))
                self.assertFalse(_is_concrete_suggestion_answer_request(request))

    def test_internal_file_reference_is_removed_from_verified_context(self):
        value = _sanitize_related_record_value(
            "Temos de completar o histórico solidset://file/"
            "5f90f567-699f-4cf2-ad8e-dc4758f99759"
        )
        self.assertEqual("Temos de completar o histórico", value)

    def test_related_guidance_rejects_direct_summary_and_internal_reference(self):
        context = (
            "Task: T-26-2448 — colocar meeting na Grid\n"
            "Description: Mostrar o meeting associado no histórico da tarefa."
        )
        self.assertFalse(_related_guidance_is_useful(
            "Tarefa relacionada: T-26-2448. Descrição verificada: Mostrar o meeting.",
            "Dê-me sugestões de estudo", context,
        ))
        self.assertFalse(_related_guidance_is_useful(
            "Analisa o anexo solidset://file/5f90f567-699f-4cf2-ad8e-dc4758f99759.",
            "Analisa esta tarefa", context,
        ))
        self.assertTrue(_related_guidance_is_useful(
            "Sugiro primeiro confirmar a origem do meeting e depois validar o que a Grid "
            "deve mostrar quando não existe associação.",
            "Dê-me sugestões de estudo", context,
        ))
        self.assertFalse(_related_guidance_is_useful(
            "Recomendo a criação de um guia ou manual interno detalhado. Primeiro, qual é "
            "a plataforma utilizada, por exemplo SolidSET?",
            "Dê-me sugestões de estudo", context + " Sistema: SolidSET.",
        ))
        self.assertFalse(_related_guidance_is_useful(
            "Para analisar esta tarefa, é necessário ter informações adicionais sobre as "
            "colunas disponíveis na Grid. Preciso saber quais são as responsabilidades atuais.",
            "Porquê é que não pode realizar uma análise desta tarefa para mim?", context,
        ))

    def test_related_record_reasoner_uses_only_the_requested_language(self):
        captured = []

        class FakeProvider:
            provider = "test"
            model = "test-model"

        class FakeLLM:
            def invoke(self, messages):
                captured.append(messages)
                return type("Result", (), {"content": '["ok"]'})()

        expectations = {
            "pt": ("És um analista", "PEDIDO ATUAL", "PETICIÓN ACTUAL"),
            "es": ("Eres un analista", "PETICIÓN ACTUAL", "CURRENT REQUEST"),
            "en": ("You are an isolated", "CURRENT REQUEST", "PEDIDO ATUAL"),
        }
        with patch(
            "app.main.agent.get_llm_for_metadata",
            return_value=(FakeLLM(), None, FakeProvider()),
        ):
            for language, (system_marker, prompt_marker, forbidden_marker) in expectations.items():
                _reason_about_related_record(
                    request_text="request", record_context="record",
                    research_context="", language=language, metadata={},
                )
                messages = captured[-1]
                self.assertIn(system_marker, messages[0].content)
                self.assertIn(prompt_marker, messages[1].content)
                self.assertNotIn(forbidden_marker, messages[1].content)

    def test_related_guidance_fallback_is_analysis_not_description_copy(self):
        result = _related_guidance_fallback(
            "Task: T-26-2448 — colocar meeting na Grid\n"
            "Description: Mostrar o meeting associado no histórico da tarefa.",
            "pt",
        )
        self.assertIn("Análise de T-26-2448", result)
        self.assertIn("origem do dado", result)
        self.assertNotIn("Descrição verificada", result)

    def test_chat_meeting_grid_fallback_is_specific_to_verified_task(self):
        result = _related_guidance_fallback(
            "Task: T-26-2448 — Tarefas - form de registo - Tab Chats - colocar na Grid coluna com meeting\n"
            "Description: Fazer constar esta informação no histórico da tarefa.",
            "pt",
        )
        self.assertIn("relação liga cada chat da tarefa ao meeting", result)
        self.assertIn("quando o chat não tiver meeting", result)
        self.assertIn("ordenação e filtragem da Grid", result)
        self.assertNotIn("guia", result.casefold())

    def test_user_request_language_overrides_portuguese_record_context(self):
        self.assertEqual(
            "es", _suggestion_request_language(
                "Investiga como poder resolver esta tarea", "pt"
            )
        )

    def test_research_command_is_not_learned_as_a_fact(self):
        self.assertEqual(
            "", _extract_learnable_suggestion_fact(
                "Investiga como poder resolver esta tarea"
            )
        )
        self.assertFalse(usable_agent_knowledge(
            "Investiga como poder resolver esta tarea", USER_ASSERTION_SOURCE
        ))

    def test_task_advice_preloads_verified_operational_context(self):
        with patch.object(
            MachiningAgent,
            "_resolve_resource_tasks_from_db",
            return_value="Tareas verificadas: T-1 y T-2",
        ):
            result = _verified_suggestion_business_context(
                {"Code": "test"},
                "Que tareas tiene asignado el recurso Alejandro Veitia",
            )

        self.assertEqual("Tareas verificadas: T-1 y T-2", result)

    def test_suggestions_progressively_narrow(self):
        self.assertEqual(4, _suggestion_count(initial=True, completed_turns=0))
        self.assertEqual(3, _suggestion_count(initial=False, completed_turns=1))
        self.assertEqual(2, _suggestion_count(initial=False, completed_turns=2))
        self.assertEqual(1, _suggestion_count(initial=False, completed_turns=10))

    def test_initial_summary_has_separate_localized_title(self):
        self.assertEqual(
            "Resumo dos temas discutidos:",
            _suggestion_title("pt", initial=True),
        )
        self.assertIsNone(_suggestion_title("pt", initial=False))

    def test_swagger_contains_framework_message_examples_for_related_endpoints(self):
        schema = app.openapi()
        endpoints = (
            "/api/v1/agent/notification/framework-message",
            "/api/v1/agent/notification/framework-message/preview",
            "/api/v1/agent/dialogue",
        )
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                examples = schema["paths"][endpoint]["post"]["requestBody"]["content"][
                    "application/json"
                ]["examples"]
                payload = examples["meetingAgentQuestion"]["value"]
                self.assertEqual(990100, payload["Chat"]["idChat2"])
                self.assertTrue(payload["Chat"]["rawMessage"])
                selected = [
                    item
                    for item in payload["Chat"]["destiny"]
                    if item.get("type") == 2 and item.get("talkWithAgent") is True
                ]
                self.assertEqual(1, len(selected))

    def test_parses_distinct_json_suggestions(self):
        result = _parse_chat_question_suggestions(
            '["Resposta direta.", "Resposta breve.", "Resposta colaborativa."]'
        )

        self.assertEqual(
            ["Resposta direta.", "Resposta breve.", "Resposta colaborativa."],
            result,
        )

    def test_parses_fenced_object_and_removes_duplicates(self):
        result = _parse_chat_question_suggestions(
            '```json\n{"suggestions":[{"text":"Sim."},{"text":"Sim."},{"text":"Claro."}]}\n```'
        )

        self.assertEqual(["Sim.", "Claro."], result)

    def test_rejects_model_length_error_as_suggestion(self):
        result = _parse_chat_question_suggestions(
            "⚠️ La consulta es demasiado larga. Por favor, reduce tu mensaje."
        )

        self.assertEqual([], result)

    def test_rejects_summary_or_embedded_topic_list(self):
        result = _parse_chat_question_suggestions(
            '["Resumo da conversa: temas discutidos", '
            '"1- Música\\n2- Futebol", '
            '"Podemos aprofundar o impacto desta decisão no próximo prazo?"]'
        )

        self.assertEqual(
            ["Podemos aprofundar o impacto desta decisão no próximo prazo?"],
            result,
        )

    def test_related_guidance_accepts_one_reasoned_plan_with_internal_steps(self):
        result = _parse_chat_question_suggestions(
            '["Para T-26-11246, primero analiza el flujo actual.\\n'
            '1. Define quién crea el meeting.\\n2. Valida los participantes."]',
            limit=1,
            allow_internal_list=True,
        )
        self.assertEqual(1, len(result))
        self.assertIn("Valida los participantes", result[0])

    def test_related_guidance_recovers_single_array_with_unescaped_quotes(self):
        result = _parse_chat_question_suggestions(
            '["Analiza quién puede crear el "meeting" y qué recursos participan."]',
            limit=1,
            allow_internal_list=True,
        )
        self.assertEqual(
            ['Analiza quién puede crear el "meeting" y qué recursos participan.'],
            result,
        )

    def test_related_guidance_unwraps_small_model_string_object(self):
        result = _parse_chat_question_suggestions(
            '''["{'String': 'Define responsables, permisos y recursos del meeting.'}"]''',
            limit=1,
            allow_internal_list=True,
        )
        self.assertEqual(
            ["Define responsables, permisos y recursos del meeting."], result
        )

    def test_safe_fallback_preserves_language_and_requested_count(self):
        result = _safe_chat_question_fallback("pt", 2)

        self.assertEqual(2, len(result))
        self.assertTrue(all("canal" in item or "conversa" in item for item in result))

    def test_safe_fallback_uses_concrete_same_resource_channel_context(self):
        result = _safe_chat_question_fallback(
            "pt", 2,
            "[2026-09-01 10:00] Victor: Rever o controlo dimensional da célula.\n"
            "[2026-09-01 10:05] Alejandro: Validar a integração do robô.",
        )
        self.assertEqual(2, len(result))
        self.assertIn("integração do robô", result[0])
        self.assertIn("controlo dimensional", result[1])
        self.assertNotIn("qual dos temas", " ".join(result).casefold())

    def test_distinguishes_task_proposal_from_task_listing(self):
        self.assertTrue(_is_business_recommendation_request(
            "¿Qué tarea debería ponerle al recurso Alejandro Veitia?"
        ))
        self.assertTrue(_is_business_recommendation_request(
            "¿Qué me propones para ponerle como nueva tarea?"
        ))
        self.assertFalse(_is_business_recommendation_request(
            "¿Qué tareas tiene asignadas Alejandro Veitia?"
        ))

    def test_concrete_answers_are_separated_from_recommendations(self):
        self.assertTrue(_is_concrete_suggestion_answer_request(
            "Qual é a temperatura atual em Leiria?"
        ))
        self.assertTrue(_is_concrete_suggestion_answer_request(
            "¿Qué tareas tiene asignadas Alejandro Veitia?"
        ))
        self.assertFalse(_is_concrete_suggestion_answer_request(
            "¿Qué tarea debería ponerle a Alejandro Veitia?"
        ))
        self.assertTrue(_is_concrete_suggestion_answer_request(
            "¿Cuál es la capital de Francia?"
        ))

    def test_parser_accepts_single_string_object(self):
        self.assertEqual(
            ["A temperatura atual é 22 °C."],
            _parse_chat_question_suggestions(
                '{"string":"A temperatura atual é 22 °C."}', limit=1
            ),
        )

    def test_extracts_verifiable_fact_but_not_drafting_instruction(self):
        fact = "Alejandro Veitia llegó a Leiria el día 17 de julio de 2026"

        self.assertEqual(fact, _extract_learnable_suggestion_fact(fact))
        self.assertEqual(
            "Alejandro Veitia trabaja habitualmente desde Leiria",
            _extract_learnable_suggestion_fact(
                "Alejandro Veitia trabaja habitualmente desde Leiria"
            ),
        )
        self.assertEqual(
            "",
            _extract_learnable_suggestion_fact("Haz la primera sugerencia más corta"),
        )
        self.assertEqual(
            "",
            _extract_learnable_suggestion_fact("¿Qué día llegó Alejandro Veitia?"),
        )

    def test_legacy_generated_drafts_are_not_usable_as_agent_facts(self):
        self.assertFalse(usable_agent_knowledge(
            "Desculpe-me, não tenho informações específicas sobre quando chegou.",
            LEGACY_SUGGESTION_SOURCE,
        ))
        self.assertFalse(usable_agent_knowledge(
            "2- Poderíamos procurar mais informações em fontes oficiais.",
            LEGACY_SUGGESTION_SOURCE,
        ))
        self.assertTrue(usable_agent_knowledge(
            "Alejandro Veitia chegou a Leiria a 17 de julho de 2026.",
            LEGACY_SUGGESTION_SOURCE,
        ))
        self.assertTrue(usable_agent_knowledge(
            "Alejandro Veitia é cubano, tem 36 anos, é casado e tem dois filhos.",
            USER_ASSERTION_SOURCE,
        ))

    def test_generic_concrete_answer_rejects_redirects(self):
        self.assertTrue(MachiningAgent._is_deflecting_concrete_answer(
            "Puede consultar un sitio especializado para obtener el dato."
        ))
        self.assertFalse(MachiningAgent._is_deflecting_concrete_answer(
            "El valor verificado es 42, actualizado a las 12:00."
        ))
        self.assertTrue(MachiningAgent._is_deflecting_concrete_answer(
            "Não tenho informações específicas sobre quando chegou."
        ))
        self.assertTrue(MachiningAgent._is_deflecting_concrete_answer(
            "No encontré información sobre Robotea; proporcione más detalles."
        ))
        self.assertEqual(
            "El valor es 42.",
            MachiningAgent._extract_concrete_answer('{"answer":"El valor es 42."}'),
        )

    def test_detects_and_cleans_incomplete_markdown_response(self):
        incomplete = "Há previsão para os próximos dias. Para consultar o detalhe no site ["

        self.assertTrue(MachiningAgent._has_incomplete_response_markup(incomplete))
        self.assertEqual(
            "Há previsão para os próximos dias.",
            MachiningAgent._discard_incomplete_response_tail(incomplete),
        )

    def test_detects_mixed_language_suggestion(self):
        mixed = (
            "Eso dependerá de tus preferencias; perhaps try something adventurous. "
            "If you have a destination in mind, let me know."
        )

        self.assertFalse(_suggestion_language_is_consistent(mixed, "es"))
        self.assertTrue(
            _suggestion_language_is_consistent(
                "Dependerá de tus preferencias, presupuesto y fechas disponibles.",
                "es",
            )
        )

    def test_short_portuguese_request_keeps_portuguese(self):
        agent_instance = MachiningAgent.__new__(MachiningAgent)
        self.assertEqual(
            "pt", agent_instance._detect_user_language("Fale-me sobre Robotea")
        )

    def test_channel_context_stays_below_prompt_budget_and_keeps_newest(self):
        rows = [
            {
                "message": f"mensaje reciente {index} " + ("x" * 400),
                "sender_full_name": "Ana",
                "timestamp": None,
            }
            for index in range(30)
        ]

        rendered = _format_suggestion_scope_context(rows)

        self.assertLessEqual(len(rendered), 3200)
        self.assertIn("mensaje reciente 0", rendered)

    def test_separates_requester_from_quoted_author(self):
        payload = {
            "Sender": {
                "session": "759f278c-041a-4fd1-b53d-96cc6487d8cc",
                "login": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                "resource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
            },
            "Chat": {
                "idChat2": 1824967,
                "idSender": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                "idSenderResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                "idWorkRoom": "debf64b2-3b3e-eb11-870c-d850e63f5833",
                "chatQuestionMessage": 1824966,
                "chatQuestion": {
                    "idChat2": 1824966,
                    "idSender": "2769fb41-1a0c-4ecf-bf54-17deed50d8b4",
                    "idSenderResource": "2dcf6097-a582-4dc3-b4be-d53bb0897461",
                    "rawMessage": "He entendido la propuesta. ¿Confirmas el plazo?",
                    "idMeeting": "7d724ee5-4a56-43cc-a87f-e9f276cbbc01",
                },
            },
            "Info": {"meeting_code": "M11"},
        }

        result = _chat_question_suggestion_context(payload)

        self.assertEqual("1824967", result["request_id"])
        self.assertEqual("1824966", result["quoted_chat_id"])
        self.assertEqual(
            "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
            result["requester_resource"],
        )
        self.assertEqual(
            "2dcf6097-a582-4dc3-b4be-d53bb0897461",
            result["quoted_resource"],
        )
        self.assertEqual("He entendido la propuesta. ¿Confirmas el plazo?", result["quoted_message"])
        self.assertEqual("M11", result["meeting_code"])

    def test_suggestion_is_forced_to_private_work_route(self):
        class ExternalTopicAgent:
            @staticmethod
            def _is_general_conversation(_text):
                return False

            @staticmethod
            def _is_external_information_query(_text):
                return True

            @staticmethod
            def _is_internal_domain_query(_text):
                return False

        orchestrator = SolidSETOrchestrator.__new__(SolidSETOrchestrator)
        orchestrator.agent = ExternalTopicAgent()

        result = orchestrator._classify({
            "session_id": "suggestion-test",
            "user_text": "¿Cuál fue el último resultado deportivo?",
            "message_metadata": {"response_suggestion_mode": True},
        })

        self.assertEqual("work_sql_rag", result["route"])

    def test_relevant_private_knowledge_prevents_external_web_route(self):
        class ExternalTopicAgent:
            @staticmethod
            def _is_general_conversation(_text):
                return False

            @staticmethod
            def _is_business_knowledge_query(_text):
                return False

            @staticmethod
            def _is_external_information_query(_text):
                return True

            @staticmethod
            def _is_internal_domain_query(_text):
                return False

        orchestrator = SolidSETOrchestrator.__new__(SolidSETOrchestrator)
        orchestrator.agent = ExternalTopicAgent()
        result = orchestrator._classify({
            "session_id": "private-fact-test",
            "user_text": "Quando chegou Alejandro a Leiria?",
            "message_metadata": {
                "agent_relevant_knowledge": (
                    "Alejandro chegou a Leiria a 17 de julho de 2026."
                )
            },
        })

        self.assertEqual("work_sql_rag", result["route"])

    def test_task_suggestion_is_classified_for_live_sql_grounding(self):
        machining_agent = MachiningAgent.__new__(MachiningAgent)
        quoted = "Que tareas tiene asignado el recurso Alejandro Veitia"
        self.assertTrue(machining_agent._is_business_knowledge_query(quoted))
        self.assertTrue(machining_agent._requires_live_business_data(quoted))
        self.assertEqual(
            ["SysResources", "SysLogin", "SysResource2Agent", "SysTask"],
            machining_agent._business_schema_table_hints(quoted),
        )
        self.assertEqual(
            {"query_sql_server", "get_db_schema"},
            _suggestion_tool_allowlist(
                quoted, ambient_mode=False, advice_refine=False
            ),
        )
        self.assertEqual(
            set(),
            _suggestion_tool_allowlist(
                quoted, ambient_mode=True, advice_refine=False
            ),
        )
        self.assertEqual(
            {"google_web_search"},
            _suggestion_tool_allowlist(
                "¿Qué tiempo hará mañana en Lisboa?",
                ambient_mode=False,
                advice_refine=False,
            ),
        )
        self.assertEqual(
            set(),
            _suggestion_tool_allowlist(
                "Explica el procedimiento interno de mantenimiento",
                ambient_mode=False,
                advice_refine=False,
            ),
        )

    def test_portugal_date_uses_configured_region(self):
        response = _local_temporal_response(
            "Que dia é hoje?",
            time_zone="Europe/Lisbon",
            locale="pt-PT",
            country_code="PT",
        )

        self.assertIsNotNone(response)
        self.assertIn("Portugal", response)
        self.assertIn("Europe/Lisbon", response)
        self.assertNotIn("Brasília", response)

    def test_spanish_date_typo_does_not_fall_through_to_llm(self):
        response = _local_temporal_response(
            "Que dias es hoy?",
            time_zone="Europe/Lisbon",
            locale="pt-PT",
            country_code="PT",
        )

        self.assertIsNotNone(response)
        self.assertTrue(response.startswith("Hoy es"))
        self.assertIn("Europe/Lisbon", response)

    def test_spanish_accented_plural_date_is_recognized(self):
        response = _local_temporal_response(
            "¿Qué días es hoy?",
            time_zone="Europe/Lisbon",
            locale="es-ES",
            country_code="ES",
        )

        self.assertIsNotNone(response)
        self.assertTrue(response.startswith("Hoy es"))

    def test_english_time_keeps_english_with_portugal_locale(self):
        response = _local_temporal_response(
            "What time is it?",
            time_zone="Europe/Lisbon",
            locale="pt-PT",
            country_code="PT",
        )

        self.assertIsNotNone(response)
        self.assertTrue(response.startswith("The local time is"))
        self.assertIn("Portugal", response)
        self.assertNotIn("A hora local", response)

    def test_payload_region_overrides_instance_default(self):
        candidates = [{"fingerprint": "message-1", "payload": {
            "Info": {
                "country_code": "ES",
                "locale": "es-ES",
                "time_zone": "Europe/Madrid",
            }
        }}]
        _attach_solidset_instance(candidates, {
            "ID": "instance-1",
            "Code": "solidset-pt",
            "BaseUrl": "http://solidset.local",
            "CountryCode": "PT",
            "Locale": "pt-PT",
            "TimeZone": "Europe/Lisbon",
        })

        self.assertEqual("ES", candidates[0]["country_code"])
        self.assertEqual("es-ES", candidates[0]["locale"])
        self.assertEqual("Europe/Madrid", candidates[0]["time_zone"])


if __name__ == "__main__":
    unittest.main()
