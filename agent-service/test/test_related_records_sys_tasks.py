import json
from unittest.mock import patch

from app.services.suggestions import (
    _chat_question_suggestion_context,
)
from app.services.related_records_context import verified_sys_tasks_context
from app.services.related_records_context import (
    build_related_record_prompt_context,
    related_record_response_needs_repair,
)


def test_nested_chat_related_records_are_read_and_deduplicated():
    module_id = "241a28aa-2dd2-481a-bfe0-86c800c45ed5"
    payload = {
        "Chat": {
            "idChat2": 1770245,
            "relatedRecords": [
                {
                    "idRecordModule": module_id,
                    "gidRecord": "af9b433e-f36b-1410-81eb-001be220af91",
                    "recordCode": "T-26-2330",
                    "recordShortName": "Crear trabajo con kubernate",
                    "recordTypeName": "Tarefa",
                },
            ],
        },
        "RelatedRecordsData": [
            {
                "idRecordModule": module_id,
                "gidRecord": "af9b433e-f36b-1410-81eb-001be220af91",
                "recordCode": "T-26-2330",
            },
            {
                "idRecordModule": module_id,
                "gidRecord": "9aa7433e-f36b-1410-81ec-001be220af91",
                "recordCode": "T-26-2331",
            },
        ],
    }

    context = _chat_question_suggestion_context(payload)

    assert len(context["related_records"]) == 2
    assert context["related_records"][0]["recordModuleName"] == "SysTasks"
    assert context["related_records"][0]["recordModuleKnown"] == "true"


@patch("app.services.related_records_context.query_sql_server")
def test_sys_tasks_related_records_use_fixed_parameterized_projection(query_tool):
    query_tool.invoke.return_value = json.dumps([{
        "IDTask": "af9b433e-f36b-1410-81eb-001be220af91",
        "Code": "T-26-2330",
        "ShortName": "Crear trabajo con kubernate",
    }])
    record = {
        "idRecordModule": "241a28aa-2dd2-481a-bfe0-86c800c45ed5",
        "gidRecord": "af9b433e-f36b-1410-81eb-001be220af91",
        "recordCode": "T-26-2330",
        "recordShortName": "Crear trabajo con kubernate",
        "recordTypeName": "Task",
    }

    context = verified_sys_tasks_context({"ID": "instance-1"}, [record])

    request = query_tool.invoke.call_args.args[0]
    assert "FROM dbo.SysTask WHERE IDTask IN (%s)" in request["query"]
    assert json.loads(request["parameters_json"]) == [record["gidRecord"]]
    assert "Code: T-26-2330" in context
    assert "SQL_VERIFICATION: retrieved" in context


def test_payload_task_is_used_as_fallback_and_forces_entity_focus():
    record = {
        "idRecordModule": "241a28aa-2dd2-481a-bfe0-86c800c45ed5",
        "gidRecord": "af9b433e-f36b-1410-81eb-001be220af91",
        "recordCode": "T-26-2330",
        "recordShortName": "Crear trabajo con kubernate",
        "recordTypeName": "Tarefa",
    }
    context = build_related_record_prompt_context(
        {"task": {
            "code": "T-26-2330",
            "shortName": "Crear trabajo con kubernate",
            "description": "Preparar el despliegue.",
            "statusStr": "Accepted",
        }},
        [record],
        "Tarefa [SysTasks]: T-26-2330 — Crear trabajo con kubernate\n"
        "SQL_VERIFICATION: task query failed; client reference only.",
    )

    assert "PAYLOAD_TASK_FALLBACK" in context
    assert "Preparar el despliegue" in context
    assert "No preguntes nuevamente cuál es la tarea" in context
    assert related_record_response_needs_repair(
        "Necesito saber cuál es la tarea concreta.", [record]
    )
