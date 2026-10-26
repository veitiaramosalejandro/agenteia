"""Resolve SolidSET file references into safe multimodal model inputs."""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any, Iterable

from app.agent.tools import query_sql_server
from app.config import settings
from app.connectors.solidset_sql import instance_context as solidset_sql_instance_context


_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
SOLIDSET_FILE_REFERENCE_RE = re.compile(
    rf"(?<![\w-])solidset://file/(?P<guid>{_UUID})(?![\w-])",
    re.IGNORECASE,
)

# Keep the transport bounded. The model receives the image as an image part,
# not as prompt text, but an accidentally huge database object must still not
# exhaust the worker or the provider request.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_AUDIO_BYTES = 32 * 1024 * 1024


def extract_solidset_file_guids(*values: Any) -> list[str]:
    """Return UUIDs in message order, without duplicate attachments."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        for match in SOLIDSET_FILE_REFERENCE_RE.finditer(value):
            guid = match.group("guid").lower()
            if guid not in seen:
                seen.add(guid)
                result.append(guid)
    return result


def _payload_value(value: dict[str, Any], *names: str) -> Any:
    wanted = {name.casefold() for name in names}
    for key, item in value.items():
        if str(key).casefold() in wanted:
            return item
    return None


def extract_solidset_audio_media_ids(*values: Any) -> list[str]:
    """Find voice-note/media IDs in FrameworkMessage and its extraData.

    The notification payload may carry the voice note as JSON text in
    ``extraData`` or as a related record with ``recordCode=VOICE``.  Only
    identifiers carrying an audio marker are accepted; arbitrary UUIDs in a
    chat payload are not treated as media.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(identifier: Any) -> None:
        normalized = str(identifier or "").strip().lower()
        if re.fullmatch(_UUID, normalized) and normalized not in seen:
            seen.add(normalized)
            found.append(normalized)

    def visit(value: Any, *, forced_audio: bool = False) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text[:1] in {"{", "["}:
                try:
                    visit(json.loads(text), forced_audio=forced_audio)
                except (TypeError, ValueError):
                    return
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                visit(item, forced_audio=forced_audio)
            return
        if not isinstance(value, dict):
            return

        voice_note = _payload_value(value, "voiceNote", "voice_note")
        if voice_note is not None:
            visit(voice_note, forced_audio=True)

        mime = str(_payload_value(value, "MimeType", "mimeType") or "").strip().lower()
        media_kind = str(_payload_value(value, "MediaKind", "mediaKind") or "").strip().lower()
        record_code = str(_payload_value(value, "recordCode", "RecordCode") or "").strip().lower()
        record_type = str(_payload_value(value, "recordTypeName", "RecordTypeName") or "").strip().lower()
        is_audio = forced_audio or mime.startswith("audio/") or media_kind in {
            "audio", "voice", "voicenote", "voice_note",
        } or record_code == "voice" or "áudio" in record_type or "audio" in record_type
        if is_audio:
            add(_payload_value(value, "id", "ID", "gidRecord", "GidRecord"))

        for name in ("extraData", "ExtraData", "relatedRecordsData", "relatedRecords", "chat", "Chat"):
            nested = _payload_value(value, name)
            if nested is not None:
                visit(nested)

    for value in values:
        visit(value)
    return found


def _row_value(row: dict[str, Any], *names: str) -> Any:
    wanted = {name.casefold() for name in names}
    for key, value in row.items():
        if str(key).casefold() in wanted:
            return value
    return None


def _decode_file_content(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, dict):
        encoded = value.get("base64") or value.get("Base64") or value.get("__base64__")
        if encoded:
            return base64.b64decode(str(encoded), validate=True)
    text = str(value).strip()
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    if text.startswith("0x"):
        try:
            return bytes.fromhex(text[2:])
        except ValueError:
            return b""
    # SolidSET may expose varbinary FileContent as hexadecimal text without
    # the usual ``0x`` prefix.  Decode that representation before trying
    # Base64; otherwise a valid PNG/JPEG is transformed into invalid bytes and
    # Ollama rejects it with "Failed to load image or audio file".
    if len(text) % 2 == 0 and text and re.fullmatch(r"[0-9a-fA-F]+", text):
        try:
            return bytes.fromhex(text)
        except ValueError:
            return b""
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        # A text value is not a valid image payload. Do not manufacture a
        # data URL that would make the model treat an error as an image.
        return b""


def _attachment_metadata(guid: str, row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    filename = str(_row_value(row, "FileName") or guid).strip()
    mime_type = str(_row_value(row, "MimeType") or "").strip().lower()
    file_size = _row_value(row, "FileSize")
    try:
        file_size = int(file_size) if file_size is not None else None
    except (TypeError, ValueError):
        file_size = None
    return {
        "guid": guid,
        "filename": filename,
        "mime_type": mime_type,
        "file_size": file_size,
        "creation_date": str(_row_value(row, "CreationDate") or ""),
        "modified_date": str(_row_value(row, "ModifiedDate") or ""),
    }


def _media_metadata(media_id: str, row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    mime_type = str(_row_value(row, "MimeType") or "audio/mp4").strip().lower()
    filename = str(_row_value(row, "FileName") or media_id).strip()
    size = _row_value(row, "SizeBytes")
    try:
        size = int(size) if size is not None else None
    except (TypeError, ValueError):
        size = None
    return {
        "guid": media_id,
        "media_id": media_id,
        "media_kind": "audio",
        "filename": filename,
        "mime_type": mime_type,
        "bucket": str(_row_value(row, "Bucket") or settings.MINIO_BUCKET_VOICE).strip(),
        "object_key": str(_row_value(row, "ObjectKey") or "").strip(),
        "duration_sec": _row_value(row, "DurationSec"),
        "file_size": size,
        "status_value": str(_row_value(row, "Status") or "").strip(),
    }


def resolve_solidset_audio_attachments(
    instance: dict[str, Any] | None,
    *message_values: Any,
) -> list[dict[str, Any]]:
    """Read a voice note segment and download its private MinIO object."""
    media_ids = extract_solidset_audio_media_ids(*message_values)
    if not media_ids:
        return []
    if not instance:
        return [{"guid": media_id, "media_id": media_id, "media_kind": "audio",
                 "status": "instance_unavailable"} for media_id in media_ids]

    placeholders = ", ".join("%s" for _ in media_ids)
    query = (
        "SELECT ID, ScopeType, ScopeKey, IDChat, IDResource, IDLogin, MediaKind, "
        "Producer, Status, Bucket, ObjectKey, MimeType, FileName, DurationSec, "
        "SizeBytes, CreatedOnUtc, ModifiedOnUtc, LiveKitRoom, LiveKitIdentity "
        "FROM dbo.SysChat2MediaSegment WHERE ID IN (" + placeholders + ") "
        "ORDER BY CreatedOnUtc DESC"
    )
    try:
        with solidset_sql_instance_context(instance):
            raw = query_sql_server.invoke({
                "query": query,
                "parameters_json": json.dumps(media_ids),
            })
        rows = json.loads(str(raw))
        rows = rows if isinstance(rows, list) else []
    except Exception as exc:
        print(
            "SOLIDSET_AUDIO_SEGMENTS_QUERY_FAILED "
            f"requested={len(media_ids)} type={type(exc).__name__}",
            flush=True,
        )
        return [{"guid": media_id, "media_id": media_id, "media_kind": "audio",
                 "status": "query_failed"} for media_id in media_ids]

    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifier = str(_row_value(row, "ID") or "").strip().lower()
        if identifier and identifier not in by_id:
            by_id[identifier] = row

    attachments: list[dict[str, Any]] = []
    client = None
    try:
        max_audio_bytes = max(
            64 * 1024,
            int(getattr(settings, "MINIO_MAX_AUDIO_BYTES", MAX_AUDIO_BYTES)),
        )
    except (TypeError, ValueError):
        max_audio_bytes = MAX_AUDIO_BYTES
    for media_id in media_ids:
        row = by_id.get(media_id)
        metadata = _media_metadata(media_id, row)
        if not row:
            metadata["status"] = "not_found"
            attachments.append(metadata)
            continue
        if not metadata["object_key"]:
            metadata["status"] = "object_key_unavailable"
            attachments.append(metadata)
            continue
        if not settings.MINIO_ACCESS_KEY or not settings.MINIO_SECRET_KEY:
            metadata["status"] = "storage_unconfigured"
            attachments.append(metadata)
            continue
        try:
            if client is None:
                from minio import Minio
                client = Minio(
                    settings.MINIO_ENDPOINT,
                    access_key=settings.MINIO_ACCESS_KEY,
                    secret_key=settings.MINIO_SECRET_KEY,
                    secure=settings.MINIO_USE_SSL,
                    cert_check=settings.MINIO_CERT_CHECK,
                )
            response = client.get_object(metadata["bucket"], metadata["object_key"])
            try:
                content = response.read(max_audio_bytes + 1)
            finally:
                response.close()
                response.release_conn()
            if len(content) > max_audio_bytes:
                metadata["status"] = "too_large"
                metadata["file_size"] = len(content)
                attachments.append(metadata)
                continue
            metadata.update({
                "status": "resolved",
                "file_size": len(content),
                "data_url": f"data:{metadata['mime_type']};base64,"
                f"{base64.b64encode(content).decode('ascii')}",
            })
        except Exception as exc:
            metadata["status"] = "download_failed"
            print(
                "SOLIDSET_AUDIO_OBJECT_DOWNLOAD_FAILED "
                f"media_id={media_id} type={type(exc).__name__}",
                flush=True,
            )
        attachments.append(metadata)
    resolved = sum(1 for item in attachments if item.get("status") == "resolved")
    print(
        "SOLIDSET_AUDIO_RESOLVED "
        f"requested={len(media_ids)} resolved={resolved} "
        f"unavailable={len(attachments) - resolved}",
        flush=True,
    )
    return attachments


def resolve_solidset_file_attachments(
    instance: dict[str, Any] | None,
    *message_values: Any,
) -> list[dict[str, Any]]:
    """Read referenced files from SysFilesSystem and prepare image data URLs.

    Resolution failures are represented in metadata and never cause a normal
    chat turn to disappear. The model can then state that the image could not
    be read instead of inventing what it contains.
    """
    guids = extract_solidset_file_guids(*message_values)
    if not guids:
        return []
    if not instance:
        return [{"guid": guid, "status": "instance_unavailable"} for guid in guids]

    placeholders = ", ".join("%s" for _ in guids)
    query = (
        "SELECT Guid, FileName, CreationDate, FileContent, ModifiedDate, MimeType, FileSize "
        "FROM dbo.SysFilesSystem WHERE Guid IN (" + placeholders + ") "
        "ORDER BY CreationDate DESC"
    )
    try:
        with solidset_sql_instance_context(instance):
            raw = query_sql_server.invoke({
                "query": query,
                "parameters_json": json.dumps(guids),
            })
        rows = json.loads(str(raw))
        if not isinstance(rows, list):
            rows = []
    except Exception as exc:
        print(
            "SOLIDSET_ATTACHMENTS_QUERY_FAILED "
            f"guids={len(guids)} type={type(exc).__name__}",
            flush=True,
        )
        return [{"guid": guid, "status": "query_failed"} for guid in guids]

    by_guid: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        guid = str(_row_value(row, "Guid") or "").strip().lower()
        if guid and guid not in by_guid:
            by_guid[guid] = row

    attachments: list[dict[str, Any]] = []
    for guid in guids:
        row = by_guid.get(guid)
        metadata = _attachment_metadata(guid, row)
        if not row:
            metadata["status"] = "not_found"
            attachments.append(metadata)
            continue
        mime_type = metadata["mime_type"]
        if not mime_type.startswith("image/"):
            metadata["status"] = "not_image"
            attachments.append(metadata)
            continue
        content = _decode_file_content(_row_value(row, "FileContent"))
        if not content:
            metadata["status"] = "content_unavailable"
            attachments.append(metadata)
            continue
        if len(content) > MAX_IMAGE_BYTES:
            metadata["status"] = "too_large"
            metadata["file_size"] = len(content)
            attachments.append(metadata)
            continue
        metadata.update({
            "status": "resolved",
            "file_size": len(content),
            "data_url": f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}",
        })
        attachments.append(metadata)
    resolved = sum(1 for item in attachments if item.get("status") == "resolved")
    print(
        "SOLIDSET_ATTACHMENTS_RESOLVED "
        f"requested={len(guids)} resolved={resolved} "
        f"unavailable={len(attachments) - resolved}",
        flush=True,
    )
    return attachments


def attachment_prompt_metadata(attachments: Iterable[dict[str, Any]]) -> str:
    """Describe attachments without putting binary content in the prompt."""
    lines: list[str] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unavailable")
        name = str(item.get("filename") or item.get("guid") or "archivo")
        mime = str(item.get("mime_type") or "tipo desconocido")
        size = item.get("file_size")
        size_text = f", {size} bytes" if isinstance(size, int) else ""
        lines.append(f"- {name} ({mime}{size_text}; estado={status})")
    return "\n".join(lines)


def build_multimodal_content(
    user_text: str,
    attachments: Iterable[dict[str, Any]] | None,
) -> str | list[dict[str, Any]]:
    """Build OpenAI-compatible text, image, and input-audio content parts."""
    parts: list[dict[str, Any]] = [{"type": "text", "text": str(user_text or "")}]
    for item in attachments or []:
        if not isinstance(item, dict) or item.get("status") != "resolved":
            continue
        data_url = str(item.get("data_url") or "").strip()
        if data_url:
            media_kind = str(item.get("media_kind") or "").strip().lower()
            mime_type = str(item.get("mime_type") or "").strip().lower()
            encoded = data_url.split(",", 1)[1] if "," in data_url else data_url
            if media_kind == "audio" or mime_type.startswith("audio/"):
                filename = str(item.get("filename") or "audio.wav")
                extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "wav"
                parts.append({
                    "type": "input_audio",
                    "input_audio": {"data": encoded, "format": extension},
                })
            else:
                parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts if len(parts) > 1 else str(user_text or "")


def replace_file_references_with_labels(
    text: str,
    attachments: Iterable[dict[str, Any]] | None,
) -> str:
    """Keep the user question readable while the binary travels as an image part."""
    by_guid = {
        str(item.get("guid") or "").lower(): item
        for item in attachments or []
        if isinstance(item, dict)
    }

    def repl(match: re.Match[str]) -> str:
        item = by_guid.get(match.group("guid").lower()) or {}
        return f"[imagen adjunta: {item.get('filename') or match.group('guid')}]"

    return SOLIDSET_FILE_REFERENCE_RE.sub(repl, str(text or ""))
