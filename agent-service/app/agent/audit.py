"""Optional asynchronous persistence for tool audit events."""

from __future__ import annotations

import threading
from typing import Any


class PostgresToolAudit:
    """Best-effort sink that never blocks or breaks a tool response."""

    def __call__(self, event: Any) -> None:
        thread = threading.Thread(
            target=self._save,
            args=(event,),
            daemon=True,
            name="tool-audit-save",
        )
        thread.start()

    @staticmethod
    def _save(event: Any) -> None:
        try:
            from app.connectors.db_client import save_agent_tool_audit

            save_agent_tool_audit(event)
        except Exception as exc:
            print(
                f"⚠️ No se pudo persistir auditoría de herramienta: {type(exc).__name__}",
                flush=True,
            )
