import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.api.controllers import automation as controller
from app.api.schemas.automation import (
    AutomationExecutionRequest,
    AutomationRuleRequest,
    WorkRoomAgentConfiguration,
)


class AgentAutomationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.instance_id = uuid4()
        self.resource_id = uuid4()
        self.workroom_id = uuid4()
        self.rule_id = uuid4()
        self.instance = {
            "ID": self.instance_id,
            "Code": "local-developer",
        }
        self.room = {
            "IDWorkRoom": self.workroom_id,
            "Name": "Testes",
            "active": True,
            "agents": [
                {
                    "IDResource": self.resource_id,
                    "active": True,
                    "response_order": 0,
                }
            ],
        }

    def test_channel_assignment_uses_selected_instance(self):
        with (
            patch.object(controller, "get_solidset_instance", return_value=self.instance),
            patch.object(controller, "list_instance_workrooms", return_value=[self.room]),
            patch.object(
                controller,
                "get_active_agent_identity_for_resource",
                return_value={"IDAgentResource": uuid4()},
            ),
            patch.object(
                controller,
                "configure_instance_agent_workroom",
                return_value={"IDSolidSETInstance": self.instance_id},
            ) as save,
        ):
            result = controller.set_instance_workroom_agent(
                "local-developer",
                self.workroom_id,
                self.resource_id,
                WorkRoomAgentConfiguration(active=True, response_order=7),
            )

        save.assert_called_once_with(
            self.instance_id,
            self.resource_id,
            self.workroom_id,
            active=True,
            response_order=7,
        )
        self.assertEqual("saved", result["status"])

    def test_rule_requires_active_channel_assignment(self):
        inactive_room = {**self.room, "agents": []}
        request = AutomationRuleRequest(
            IDResource=self.resource_id,
            IDWorkRoom=self.workroom_id,
            Name="Rule",
        )
        with (
            patch.object(controller, "get_solidset_instance", return_value=self.instance),
            patch.object(controller, "list_instance_workrooms", return_value=[inactive_room]),
            patch.object(
                controller,
                "get_active_agent_identity_for_resource",
                return_value={"IDAgentResource": uuid4()},
            ),
            patch.object(controller, "save_automation_rule") as save,
        ):
            with self.assertRaises(Exception) as raised:
                controller.create_automation_rule("local-developer", request)
        self.assertEqual(409, raised.exception.status_code)
        save.assert_not_called()

    def test_evaluation_reports_missing_capability_and_approval(self):
        rule = {
            "ID": self.rule_id,
            "IDResource": self.resource_id,
            "IDWorkRoom": self.workroom_id,
            "RequiredCapabilities": ["coding", "sql"],
            "MaxRunsPerHour": 5,
            "RequireApproval": True,
            "active": True,
        }
        with (
            patch.object(controller, "list_instance_workrooms", return_value=[self.room]),
            patch.object(
                controller,
                "get_agent_model_configurations",
                return_value=[{"Capabilities": ["coding"], "active": True}],
            ),
            patch.object(controller, "automation_runs_last_hour", return_value=2),
        ):
            result = controller._evaluate(self.instance, rule, approved=False)

        self.assertFalse(result["eligible"])
        self.assertIn("missing_capabilities:sql", result["reasons"])
        self.assertIn("approval_required", result["reasons"])

    async def test_delivery_without_approval_is_blocked_before_dialogue(self):
        rule = {
            "ID": self.rule_id,
            "IDResource": self.resource_id,
            "IDWorkRoom": self.workroom_id,
            "RequiredCapabilities": [],
            "MaxRunsPerHour": 5,
            "RequireApproval": False,
            "Instruction": "",
            "active": True,
        }
        request = AutomationExecutionRequest(
            Message="Prepare response",
            Approved=False,
            SendToSolidSET=True,
        )
        dialogue = AsyncMock()
        with (
            patch.object(controller, "get_solidset_instance", return_value=self.instance),
            patch.object(controller, "get_automation_rule", return_value=rule),
            patch.object(controller, "list_instance_workrooms", return_value=[self.room]),
            patch.object(controller, "get_agent_model_configurations", return_value=[]),
            patch.object(controller, "automation_runs_last_hour", return_value=0),
            patch.object(controller, "record_automation_run", return_value={"Status": "blocked"}),
            patch.object(controller, "handle_multi_agent_dialogue", dialogue),
        ):
            result = await controller.execute_automation_rule(
                "local-developer", self.rule_id, request
            )

        self.assertEqual("blocked", result["status"])
        self.assertIn("approval_required_for_delivery", result["evaluation"]["reasons"])
        dialogue.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
