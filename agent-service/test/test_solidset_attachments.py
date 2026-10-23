import base64
import json
from unittest.mock import patch

from langchain_core.messages import HumanMessage

from app.agent.prompt_budget import budget_messages
from app.services.solidset_attachments import (
    build_multimodal_content,
    extract_solidset_file_guids,
    resolve_solidset_file_attachments,
    replace_file_references_with_labels,
)


GUID = "60bfbda9-e12e-4ff5-8651-98714609f0b3"


def test_extracts_only_valid_solidset_file_references_and_deduplicates():
    text = f"Mira solidset://file/{GUID} y solidset://file/{GUID}."
    assert extract_solidset_file_guids(text) == [GUID]
    assert extract_solidset_file_guids("solidset://file/not-a-guid") == []


@patch("app.services.solidset_attachments.query_sql_server")
def test_resolves_sysfilesystem_image_to_openai_compatible_data_url(query_tool):
    content = b"fake-png-content"
    query_tool.invoke.return_value = json.dumps([{
        "Guid": GUID,
        "FileName": "captura.png",
        "MimeType": "image/png",
        "FileSize": len(content),
        "FileContent": base64.b64encode(content).decode("ascii"),
    }])

    attachments = resolve_solidset_file_attachments(
        {"ID": "instance-1"}, f"Analiza solidset://file/{GUID}"
    )

    request = query_tool.invoke.call_args.args[0]
    assert "FROM dbo.SysFilesSystem" in request["query"]
    assert "FileContent" in request["query"]
    assert json.loads(request["parameters_json"]) == [GUID]
    assert attachments[0]["status"] == "resolved"
    assert attachments[0]["filename"] == "captura.png"
    assert attachments[0]["data_url"].startswith("data:image/png;base64,")


def test_builds_image_part_and_replaces_uri_only_in_visible_text():
    attachments = [{
        "guid": GUID,
        "filename": "captura.png",
        "mime_type": "image/png",
        "status": "resolved",
        "data_url": "data:image/png;base64,ZmFrZQ==",
    }]
    content = build_multimodal_content(
        f"¿Qué muestra solidset://file/{GUID}?", attachments
    )
    assert content[0] == {"type": "text", "text": f"¿Qué muestra solidset://file/{GUID}?"}
    assert content[1]["type"] == "image_url"
    assert "captura.png" in replace_file_references_with_labels(
        f"¿Qué muestra solidset://file/{GUID}?", attachments
    )


def test_prompt_budget_preserves_image_part_instead_of_clipping_base64():
    message = HumanMessage(content=[
        {"type": "text", "text": "Analiza la imagen"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 100000}},
    ], additional_kwargs={"budget_current": True})
    bounded = budget_messages([message])
    assert isinstance(bounded[0].content, list)
    assert bounded[0].content[1]["type"] == "image_url"
