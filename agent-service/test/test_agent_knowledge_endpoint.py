import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from pydantic import ValidationError
from fastapi import HTTPException

from app.api.controllers import agent_management as controller
from app.api.schemas.common import AgentKnowledgeRequest, AgentKnowledgeSearchRequest


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

    async def test_rag_test_preserves_agent_and_instance_scope(self):
        instance_id = uuid4()
        resource_id = uuid4()
        workroom_id = uuid4()
        request = AgentKnowledgeSearchRequest(
            SolidSETInstanceCode="local-developer",
            Query="política de despliegue",
            IDWorkRoom=workroom_id,
            Limit=4,
            MinScore=0.65,
            IncludeSystemKnowledge=True,
        )
        learner = Mock()
        learner.consultar_conocimiento_agente.return_value = "privado 1\n---\nprivado 2"
        learner.consultar_conocimiento_sistema.return_value = "sistema 1"

        with (
            patch.object(
                controller,
                "get_solidset_instance",
                return_value={"ID": instance_id, "Code": "local-developer"},
            ),
            patch.object(
                controller,
                "get_active_agent_identity_for_resource",
                return_value={"IDResource": resource_id},
            ),
            patch.object(
                controller,
                "agent",
                SimpleNamespace(sistema_aprendizaje=learner),
            ),
        ):
            response = await controller.test_agent_knowledge_retrieval(
                resource_id, request
            )

        learner.consultar_conocimiento_agente.assert_called_once_with(
            request.Query,
            agent_resource_id=str(resource_id),
            solidset_instance_id=str(instance_id),
            canal_id=str(workroom_id),
            limit=4,
            min_score=0.65,
        )
        learner.consultar_conocimiento_sistema.assert_called_once_with(
            request.Query,
            solidset_instance_id=str(instance_id),
            agent_resource_id=str(resource_id),
            limit=4,
            min_score=0.65,
        )
        self.assertEqual(2, response.privateMatchCount)
        self.assertEqual(1, response.systemMatchCount)

    def test_list_uses_explicit_instance_and_agent(self):
        instance_id = uuid4()
        resource_id = uuid4()
        with (
            patch.object(
                controller,
                "get_solidset_instance",
                return_value={"ID": instance_id, "Code": "beta-solidset"},
            ),
            patch.object(
                controller,
                "get_active_agent_identity_for_resource",
                return_value={"IDResource": resource_id},
            ),
            patch.object(
                controller, "list_agent_knowledge_records", return_value=[]
            ) as list_records,
        ):
            response = controller.read_agent_knowledge(
                resource_id, "beta-solidset", activeOnly=False, limit=25
            )

        list_records.assert_called_once_with(
            resource_id, instance_id, active_only=False, limit=25
        )
        self.assertEqual(instance_id, response["instanceId"])
        self.assertEqual(resource_id, response["agentResourceId"])

    def test_vector_delete_failure_keeps_sql_source_active(self):
        instance_id = uuid4()
        resource_id = uuid4()
        knowledge_id = uuid4()
        learner = Mock()
        learner.collection = "aprendizaje"
        learner.qdrant.delete.side_effect = RuntimeError("qdrant unavailable")
        with (
            patch.object(
                controller,
                "get_solidset_instance",
                return_value={"ID": instance_id, "Code": "local-developer"},
            ),
            patch.object(
                controller,
                "get_active_agent_identity_for_resource",
                return_value={"IDResource": resource_id},
            ),
            patch.object(
                controller,
                "get_agent_knowledge_record",
                return_value={"ID": knowledge_id, "active": True},
            ),
            patch.object(controller, "deactivate_agent_knowledge") as deactivate,
            patch.object(
                controller,
                "agent",
                SimpleNamespace(sistema_aprendizaje=learner),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                controller.remove_agent_knowledge(
                    resource_id, knowledge_id, "local-developer"
                )

        self.assertEqual(503, raised.exception.status_code)
        deactivate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
