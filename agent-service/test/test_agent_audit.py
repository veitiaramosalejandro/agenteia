import unittest
from unittest.mock import patch

from app.agent.audit import PostgresToolAudit
from app.agent.contracts import ToolAuditEvent


class AgentAuditTests(unittest.TestCase):
    @patch("app.connectors.db_client.save_agent_tool_audit")
    def test_sink_delegates_event_without_changing_it(self, save):
        event = ToolAuditEvent(
            tool_name="google_web_search",
            source="external_web",
            success=True,
            elapsed_seconds=0.25,
            agent_resource_id="agent-a",
            session_id="session-a",
        )

        PostgresToolAudit._save(event)

        save.assert_called_once_with(event)

    @patch("app.connectors.db_client.save_agent_tool_audit", side_effect=RuntimeError)
    def test_sink_swallows_persistence_errors(self, save):
        event = ToolAuditEvent(
            tool_name="query_sql_server",
            source="solidset_sql",
            success=False,
            elapsed_seconds=0.1,
            error_type="RuntimeError",
        )

        PostgresToolAudit._save(event)

        save.assert_called_once_with(event)


if __name__ == "__main__":
    unittest.main()
