import hmac
import re


_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC(?:UTE)?|"
    r"GRANT|REVOKE|DENY|BACKUP|RESTORE|DBCC|SHUTDOWN|KILL|BULK|INTO|"
    r"WAITFOR|DECLARE|USE|SETUSER|RECONFIGURE|"
    r"OPENROWSET|OPENDATASOURCE|OPENQUERY)\b|\bxp_",
    re.IGNORECASE,
)


def validate_read_query(query: str) -> str:
    value = str(query or "").strip()
    if not value or not re.match(r"^(SELECT|WITH)\b", value, re.IGNORECASE):
        raise ValueError("Apenas consultas SELECT ou CTE de leitura são permitidas.")
    if "--" in value or "/*" in value or "*/" in value or ";" in value.rstrip(";"):
        raise ValueError("Comentários e múltiplas instruções SQL não são permitidos.")
    if _FORBIDDEN.search(value):
        raise ValueError("A consulta contém uma operação não permitida.")
    for match in re.finditer(
        r"\b(?:FROM|JOIN)\s+([A-Za-z0-9_\[\]\.]+)", value, re.IGNORECASE
    ):
        reference = match.group(1).replace("[", "").replace("]", "")
        if len([part for part in reference.split(".") if part]) > 2:
            raise ValueError("Referências a outras bases de dados não são permitidas.")
    return value.rstrip(";").strip()


def valid_api_key(provided: str | None, expected: str) -> bool:
    return bool(provided and expected and hmac.compare_digest(provided, expected))
