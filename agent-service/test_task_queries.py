import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from app.agent.core import MachiningAgent


class TestTaskQueries(unittest.TestCase):
    def setUp(self):
        self.agent = MachiningAgent.__new__(MachiningAgent)

    def test_task_request_is_live_sql_business_query(self):
        text = "Que tareas tiene asignado el recurso Alejandro Veitia"
        self.assertTrue(self.agent._is_sql_business_query(text))
        self.assertTrue(self.agent._requires_live_business_data(text))
        self.assertEqual(
            "Alejandro Veitia",
            self.agent._extract_task_resource_term(text),
        )

    def test_task_lookup_uses_parameter_and_sys_task_resource(self):
        rows = [{
            "ShortName": "Revisar planificación",
            "WorkStatus": 2,
            "ProgressPercentage": 50,
            "EndDate": "2026-08-30T10:00:00",
        }]
        from unittest.mock import Mock
        invoke = Mock(return_value=json.dumps(rows))
        with patch(
            "app.agent.core.query_sql_server",
            SimpleNamespace(invoke=invoke),
        ):
            response = self.agent._resolve_resource_tasks_from_db(
                "Que tareas tiene asignado el recurso Alejandro Veitia"
            )

        args = invoke.call_args.args[0]
        self.assertIn("FROM dbo.SysTask t", args["query"])
        self.assertIn("r.ResourceId = t.IDResource", args["query"])
        self.assertIn("LIKE UPPER(%s)", args["query"])
        self.assertNotIn("Alejandro Veitia", args["query"])
        self.assertEqual(["%Alejandro Veitia%"], json.loads(args["parameters_json"]))
        self.assertIn("Revisar planificación", response)
        self.assertIn("50%", response)

    def test_non_task_statement_does_not_trigger_task_query(self):
        self.assertIsNone(self.agent._extract_task_resource_term(
            "Alejandro Veitia pertenece al recurso de desarrollo"
        ))


if __name__ == "__main__":
    unittest.main()
