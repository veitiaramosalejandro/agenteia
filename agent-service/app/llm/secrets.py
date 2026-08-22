"""Encryption for credentials persisted in PostgreSQL."""

import os
from pathlib import Path
import threading
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


_key_lock = threading.Lock()


def _is_valid_fernet_key(value: str) -> bool:
    try:
        Fernet(value.encode("ascii"))
        return True
    except (ValueError, TypeError):
        return False


def _credential_key() -> str:
    configured = str(settings.LLM_CREDENTIAL_ENCRYPTION_KEY or "").strip()
    if configured and _is_valid_fernet_key(configured):
        return configured
    if configured:
        print(
            "⚠️ LLM_CREDENTIAL_ENCRYPTION_KEY inválida; será utilizada a chave "
            "persistente de data/credential.key.",
            flush=True,
        )
    path = Path(settings.CREDENTIAL_ENCRYPTION_KEY_FILE).expanduser()
    with _key_lock:
        if path.exists():
            key = path.read_text(encoding="ascii").strip()
            if key and _is_valid_fernet_key(key):
                return key
            suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            backup = path.with_name(f"{path.name}.invalid-{suffix}")
            path.replace(backup)
            print(
                f"⚠️ Chave de credenciais inválida preservada em {backup}; "
                "foi gerada uma chave Fernet nova.",
                flush=True,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        generated = Fernet.generate_key().decode()
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(generated)
        except FileExistsError:
            generated = path.read_text(encoding="ascii").strip()
            if not _is_valid_fernet_key(generated):
                raise RuntimeError("Não foi possível criar uma chave Fernet persistente válida.")
        return generated


def encrypt_api_key(value: str | None) -> str | None:
    plain = str(value or "")
    if not plain:
        return None
    key = _credential_key()
    return "fernet:" + Fernet(key.encode()).encrypt(plain.encode()).decode()


def decrypt_api_key(value: str | None) -> str:
    stored = str(value or "")
    if not stored:
        return ""
    if not stored.startswith("fernet:"):
        # Compatibilidad temporal si existiera una fila anterior a este cifrado.
        return stored
    key = _credential_key()
    try:
        return Fernet(key.encode()).decrypt(stored[7:].encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError("Não foi possível desencriptar a credencial guardada.") from exc
