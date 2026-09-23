import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from pydantic import ValidationError

from app.api.controllers import agent_management as controller
from app.api.schemas.common import AgentKnowledgeRequest


class AgentKnowledgeEndpointTests(unittest.IsolatedAsyncioTestCase):
    def test_instance_code_is_required(self):
        with self.assertRaises(ValidationError):
            AgentKnowledgeRequest(KnowledgeText="Hecho privado")

    async def test_resolves_instance_before_persisting_and_indexing(self):
        instance_id = uuid4()
        resource_id = uuid4()
        workroom_id = uuid4()
        knowledge_id = uuid4()
        request = AgentKnowledgeRequest(
            SolidSETInstanceCode="local-developer",
            IDWorkRoom=workroom_id,
            KnowledgeText="Hecho privado",
        )
        saved = {
            "ID": knowledge_id,
            "IDSolidSETInstance": instance_id,
            "IDResource": resource_id,
            "IDWorkRoom": workroom_id,
            "Title": None,
            "KnowledgeText": "Hecho privado",
            "Source": "manual",
            "Stamp": datetime.now(timezone.utc),
            "active": True,
        }
        learner = Mock()
        learner.aprender_conocimiento_agente.return_value = True

        with (
            patch.object(
                controller,
                "get_solidset_instance",
                return_value={"ID": instance_id, "Code": "local-developer"},
            ) as resolve,
            patch.object(
                controller,
                "get_active_agent_identity_for_resource",
                return_value={"IDResource": resource_id},
            ) as membership,
            patch.object(controller, "save_agent_knowledge", return_value=saved) as save,
            patch.object(
                controller,
                "agent",
                SimpleNamespace(sistema_aprendizaje=learner),
            ),
        ):
            response = await controller.create_agent_knowledge(resource_id, request)

        resolve.assert_called_once_with(code="local-developer", source_ip=None)
        membership.assert_called_once_with(resource_id, instance_id)
        payload = save.call_args.args[0]
        self.assertEqual(payload["IDSolidSETInstance"], instance_id)
        self.assertEqual(payload["IDResource"], resource_id)
        self.assertNotIn("SolidSETInstanceCode", payload)
        learner.aprender_conocimiento_agente.assert_called_once_with(saved)
        self.assertEqual(response.IDSolidSETInstance, instance_id)
        self.assertTrue(response.indexed)


if __name__ == "__main__":
    unittest.main()
