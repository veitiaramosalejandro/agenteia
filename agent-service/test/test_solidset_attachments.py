import base64
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import HumanMessage

from app.config import settings
from app.agent.prompt_budget import budget_messages
from app.services.solidset_attachments import (
    build_multimodal_content,
    extract_solidset_audio_media_ids,
    resolve_solidset_audio_attachments,
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


@patch("app.services.solidset_attachments.query_sql_server")
def test_resolves_hexadecimal_varbinary_without_0x_prefix(query_tool):
    content = b"\x89PNG\r\n\x1a\nvalid-png-payload"
    query_tool.invoke.return_value = json.dumps([{
        "Guid": GUID,
        "FileName": "captura.png",
        "MimeType": "image/png",
        "FileSize": len(content),
        "FileContent": content.hex(),
    }])

    attachments = resolve_solidset_file_attachments(
        {"ID": "instance-1"}, f"Analiza solidset://file/{GUID}"
    )

    encoded = attachments[0]["data_url"].split(",", 1)[1]
    assert attachments[0]["status"] == "resolved"
    assert base64.b64decode(encoded).startswith(b"\x89PNG\r\n\x1a\n")


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


def test_extracts_voice_note_id_from_extra_data_and_related_record():
    payload = {
        "extraData": json.dumps({
            "voiceNote": {
                "id": "15acf1c0-d7ee-4ee6-8c18-6fefa790f80c",
                "mimeType": "audio/mp4",
            }
        }),
        "relatedRecordsData": [{
            "gidRecord": "15acf1c0-d7ee-4ee6-8c18-6fefa790f80c",
            "recordCode": "VOICE",
            "recordTypeName": "Mensagem de voz",
        }],
    }
    assert extract_solidset_audio_media_ids(payload) == [
        "15acf1c0-d7ee-4ee6-8c18-6fefa790f80c"
    ]


@patch("app.services.solidset_attachments.query_sql_server")
def test_downloads_audio_segment_from_minio(query_tool):
    media_id = "15acf1c0-d7ee-4ee6-8c18-6fefa790f80c"
    audio = b"fake-m4a-content"
    query_tool.invoke.return_value = json.dumps([{
        "ID": media_id,
        "Status": 1,
        "Bucket": "chats",
        "ObjectKey": f"voice-notes/{media_id}.m4a",
        "MimeType": "audio/mp4",
        "FileName": "audio_20261023_165522.m4a",
        "DurationSec": 8,
        "SizeBytes": len(audio),
    }])

    class Response:
        def read(self, _limit):
            return audio

        def close(self):
            pass

        def release_conn(self):
            pass

    class FakeMinio:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def get_object(self, bucket, object_key):
            assert bucket == "chats"
            assert object_key == f"voice-notes/{media_id}.m4a"
            return Response()

    payload = {"extraData": json.dumps({"voiceNote": {"id": media_id}})}
    fake_module = SimpleNamespace(Minio=FakeMinio)
    with patch.dict(sys.modules, {"minio": fake_module}), \
         patch.object(settings, "MINIO_ACCESS_KEY", "access"), \
         patch.object(settings, "MINIO_SECRET_KEY", "secret"):
        attachments = resolve_solidset_audio_attachments({"ID": "instance-1"}, payload)

    assert attachments[0]["status"] == "resolved"
    assert attachments[0]["media_kind"] == "audio"
    assert base64.b64decode(attachments[0]["data_url"].split(",", 1)[1]) == audio


def test_builds_openai_input_audio_part():
    content = build_multimodal_content(
        "¿Qué se dice en este audio?",
        [{
            "media_kind": "audio",
            "mime_type": "audio/mp4",
            "filename": "nota.m4a",
            "status": "resolved",
            "data_url": "data:audio/mp4;base64,QUJD",
        }],
    )
    assert content[1] == {
        "type": "input_audio",
        "input_audio": {"data": "QUJD", "format": "m4a"},
    }


def test_prompt_budget_preserves_image_part_instead_of_clipping_base64():
    message = HumanMessage(content=[
        {"type": "text", "text": "Analiza la imagen"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 100000}},
    ], additional_kwargs={"budget_current": True})
    bounded = budget_messages([message])
    assert isinstance(bounded[0].content, list)
    assert bounded[0].content[1]["type"] == "image_url"
