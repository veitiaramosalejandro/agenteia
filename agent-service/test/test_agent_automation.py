import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.api.controllers import automation as controller
from app.services import auto_reply
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

    def test_notifications_ignore_approval_but_enforce_capabilities_and_rate(self):
        agent_identity = uuid4()
        candidate = {
            "fingerprint": "notification-approval",
            "channel_id": str(self.workroom_id),
            "solidset_instance_code": self.instance["Code"],
            "solidset_instance_id": str(self.instance_id),
            "sender_resource": str(uuid4()),
            "payload": {"Chat": {"questionType": 3, "destiny": [
                {"type": 2, "talkWithAgent": True, "resource": str(self.resource_id)}
            ]}},
        }
        configured = {
            "IDResource": self.resource_id, "IDAgentResource": agent_identity,
            "Name": "Developer", "AutomationRuleID": self.rule_id,
            "AutomationRequireApproval": True,
            "AutomationRequiredCapabilities": ["coding"], "AutomationMaxRunsPerHour": 5,
        }
        for capabilities, runs, expected in [(["coding"], 0, 1), ([], 0, 0), (["coding"], 5, 0)]:
            with (
                self.subTest(capabilities=capabilities, runs=runs),
                patch.object(auto_reply, "get_solidset_instance", return_value={**self.instance, "DataAPI": {"BaseUrl": "http://data-api"}}),
                patch.object(auto_reply, "ensure_payload_agent_workroom_assignments", return_value=0),
                patch.object(auto_reply, "get_active_agents_for_workroom", return_value=[configured]),
                patch.object(auto_reply, "verify_and_sync_solidset_agent_mapping", return_value={"verified": True, "IDAgentResource": str(agent_identity)}),
                patch.object(auto_reply, "get_agent_model_configurations", return_value=[{"Capabilities": capabilities, "active": True}]),
                patch.object(auto_reply, "automation_runs_last_hour", return_value=runs),
                patch.object(auto_reply, "get_agent_knowledge", return_value=""),
                patch.object(auto_reply, "get_agent_reinforcement_context", return_value=""),
            ):
                routed = auto_reply._route_candidates_to_selected_agents([candidate])
            self.assertEqual(expected, len(routed))
            if expected:
                self.assertEqual(str(self.rule_id), routed[0]["automation_rule_id"])

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
