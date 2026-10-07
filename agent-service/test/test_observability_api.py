import unittest
from unittest.mock import patch
from uuid import uuid4

from app.api.controllers import observability as controller


class ObservabilityAPITests(unittest.TestCase):
    def test_snapshot_is_scoped_to_selected_instance(self):
        instance_id = uuid4()
        resource_id = uuid4()
        persisted = {
            "windowHours": 24,
            "metrics": {"total": 0, "successful": 0, "failed": 0},
            "events": [],
        }
        with (
            patch.object(
                controller,
                "get_solidset_instance",
                return_value={"ID": instance_id, "Code": "beta-solidset"},
            ),
            patch.object(controller, "ensure_agent_response_audit_schema"),
            patch.object(controller, "ensure_agent_tool_audit_schema"),
            patch.object(controller, "ensure_agent_automation_schema"),
            patch.object(controller, "ensure_system_knowledge_schema"),
            patch.object(
                controller, "get_observability_snapshot", return_value=persisted
            ) as read,
            patch.object(controller, "metrics_snapshot", return_value={"count": 3}),
        ):
            result = controller._snapshot(
                "beta-solidset", 24, 50, "tool", "failed", resource_id
            )

        read.assert_called_once_with(
            instance_id,
            hours=24,
            limit=50,
            event_type="tool",
            status="failed",
            resource_id=resource_id,
        )
        self.assertEqual(instance_id, result["instanceId"])
        self.assertEqual(3, result["runtime"]["dialogue"]["count"])

    def test_csv_values_neutralize_spreadsheet_formulas(self):
        self.assertEqual("'=cmd()", controller._csv_safe("=cmd()"))
        self.assertEqual("'+SUM(A1:A2)", controller._csv_safe("+SUM(A1:A2)"))
        self.assertEqual("normal", controller._csv_safe("normal"))


if __name__ == "__main__":
    unittest.main()
