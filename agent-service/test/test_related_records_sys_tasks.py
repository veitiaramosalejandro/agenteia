import json
from unittest.mock import patch

from app.services.suggestions import (
    _chat_question_suggestion_context,
    _verified_sys_tasks_context,
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


@patch("app.services.suggestions.query_sql_server")
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

    context = _verified_sys_tasks_context({"ID": "instance-1"}, [record])

    request = query_tool.invoke.call_args.args[0]
    assert "FROM dbo.SysTask WHERE IDTask IN (%s)" in request["query"]
    assert json.loads(request["parameters_json"]) == [record["gidRecord"]]
    assert "Code: T-26-2330" in context
    assert "SQL_VERIFICATION: retrieved" in context
