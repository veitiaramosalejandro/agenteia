"""Resolve SolidSET file references into safe multimodal model inputs."""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any, Iterable

from app.agent.tools import query_sql_server
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
    """Build the OpenAI-compatible text plus image parts expected by Ollama."""
    parts: list[dict[str, Any]] = [{"type": "text", "text": str(user_text or "")}]
    for item in attachments or []:
        if not isinstance(item, dict) or item.get("status") != "resolved":
            continue
        data_url = str(item.get("data_url") or "").strip()
        if data_url:
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
