"""Cifrado de credenciales LLM persistidas en PostgreSQL."""

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def encrypt_api_key(value: str | None) -> str | None:
    plain = str(value or "")
    if not plain:
        return None
    key = str(settings.LLM_CREDENTIAL_ENCRYPTION_KEY or "").strip()
    if not key:
        raise ValueError(
            "LLM_CREDENTIAL_ENCRYPTION_KEY es obligatoria para guardar una APIKey."
        )
    return "fernet:" + Fernet(key.encode()).encrypt(plain.encode()).decode()


def decrypt_api_key(value: str | None) -> str:
    stored = str(value or "")
    if not stored:
        return ""
    if not stored.startswith("fernet:"):
        # Compatibilidad temporal si existiera una fila anterior a este cifrado.
        return stored
    key = str(settings.LLM_CREDENTIAL_ENCRYPTION_KEY or "").strip()
    if not key:
        raise RuntimeError("Falta LLM_CREDENTIAL_ENCRYPTION_KEY para usar el proveedor.")
    try:
        return Fernet(key.encode()).decrypt(stored[7:].encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError("No se pudo descifrar la credencial del proveedor LLM.") from exc
