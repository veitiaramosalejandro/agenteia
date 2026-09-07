import contextlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from app.services import openai_direct as service
from app.connectors.db_client import _postgres_connection
from app.agent.orchestrator import SolidSETOrchestrator


class DirectRoutingTests(unittest.TestCase):
    def test_direct_response_bypasses_classifier_and_local_generation(self):
        runtime = Mock()
        runtime.answer_with_assigned_openai.return_value = 'Respuesta OpenAI.'
        orchestrator = object.__new__(SolidSETOrchestrator)
        orchestrator.agent = runtime
        orchestrator.graph = Mock()
        result = orchestrator.invoke(session_id='s',user_text='Pregunta')
        self.assertEqual(result, 'Respuesta OpenAI.')
        orchestrator.graph.invoke.assert_not_called()
        runtime.language_resolver.resolve.assert_not_called()

    @patch.object(service, 'assigned_openai', return_value=None)
    @patch.object(service, 'create_chat_model')
    def test_local_only_agent_keeps_existing_route(self, create, assigned):
        self.assertIsNone(service.answer_direct('Pregunta', {}, 's'))
        create.assert_not_called()


class DirectLearningIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.db = _postgres_connection()
        self.addCleanup(self.db.close)
        self.addCleanup(self.db.rollback)
        schema = 'direct_test_' + uuid4().hex
        self.schema = schema
        self.db.execute('CREATE SCHEMA ' + schema)
        self.db.execute(self.sql('CREATE TABLE public."SysResourceIA" ("IDResource" uuid PRIMARY KEY, active boolean DEFAULT true)'))
        script = Path('app/connectors/llm_schema.sql').read_text()
        self.db.execute(self.sql(script).replace("table_schema='public'", "table_schema='"+schema+"'"))
        self.resource, self.instance = uuid4(), uuid4()
        self.db.execute(self.sql('INSERT INTO public."SysResourceIA" VALUES (%s,true)'), (self.resource,))
        for code, provider in [('remote','openai'),('local','ollama')]:
            self.db.execute(self.sql('''INSERT INTO public."SysLLMProviderConfiguration"
                ("Code","Name","Provider","Model") VALUES (%s,%s,%s,'test-model')'''), (code,code,provider))
        self.db.execute(self.sql('''INSERT INTO public."SysAgentIAModel" ("IDResource","IDProviderConfiguration","Capabilities")
            SELECT %s,"ID",'["external_web"]' FROM public."SysLLMProviderConfiguration" WHERE "Code"='remote' '''), (self.resource,))
        owner = self
        class Proxy:
            def execute(self, query, params=None):
                return owner.db.execute(owner.sql(query), params)
        @contextlib.contextmanager
        def connection():
            yield Proxy()
        for name, value in [('_postgres_connection',connection),('_ready',False)]:
            p=patch.object(service,name,value);p.start();self.addCleanup(p.stop)
        service.ensure_schema()
        self.metadata={'agent_resource_id':str(self.resource),'solidset_instance_id':str(self.instance)}

    def sql(self, query):
        return query.replace('public.', self.schema+'.')

    def rows(self):
        return self.db.execute(self.sql('SELECT * FROM public."OpenAILocalLearning"')).fetchall()

    def test_specialist_assignment_now_routes_all_messages_and_deduplicates_learning(self):
        model=Mock()
        model.invoke.return_value=AIMessage(content=[{'type':'text','text':'Respuesta original.'}])
        with patch.object(service,'create_chat_model',return_value=model):
            for _ in range(2):
                self.assertEqual(service.answer_direct('Hola',self.metadata,'session'),'Respuesta original.')
        rows=self.rows()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['answer'],'Respuesta original.')
        self.assertEqual(rows[0]['status'],'pending')

    def test_remote_failure_does_not_generate_locally_or_enqueue(self):
        model=Mock();model.invoke.side_effect=TimeoutError()
        with patch.object(service,'create_chat_model',return_value=model) as create:
            with self.assertRaisesRegex(RuntimeError, 'OpenAI no pudo completar'):
                service.answer_direct('Pregunta',self.metadata,'s')
        self.assertEqual(create.call_count,1)
        self.assertEqual(self.rows(),[])

    def test_disabled_learning_still_returns_remote_answer(self):
        self.db.execute(self.sql('UPDATE public."SysAgentIAModel" SET "TrainingMode"=\'disabled\''))
        model=Mock();model.invoke.return_value=AIMessage(content='Respuesta')
        with patch.object(service,'create_chat_model',return_value=model):
            self.assertEqual(service.answer_direct('Pregunta',self.metadata,'s'),'Respuesta')
        self.assertEqual(self.rows(),[])

    def enqueue(self):
        service.enqueue_learning(service.assigned_openai(self.resource),'Pregunta','Respuesta',self.metadata,'s')

    def test_worker_uses_local_model_and_indexes_shared_unverified_note(self):
        self.enqueue()
        model=Mock();model.invoke.return_value=AIMessage(content='Nota local.')
        learning=Mock();learning.aprender_actividad.return_value=True
        with patch.object(service,'create_chat_model',return_value=model) as create:
            self.assertTrue(service.process_one(learning))
        self.assertEqual(create.call_args.args[0].provider,'ollama')
        note=learning.aprender_actividad.call_args.args[0]
        self.assertEqual(note.metadatos['knowledge_scope'],'global_shared')
        self.assertEqual(note.metadatos['solidset_instance_id'],str(self.instance))
        self.assertFalse(note.metadatos['verified'])
        self.assertEqual(self.rows()[0]['status'],'completed')
        self.assertFalse(service.process_one(learning))

    def test_worker_failures_stop_after_three_attempts(self):
        self.enqueue()
        with patch.object(service,'create_chat_model',side_effect=TimeoutError()):
            for _ in range(3):
                self.db.execute(self.sql('UPDATE public."OpenAILocalLearning" SET available_at=now()'))
                self.assertTrue(service.process_one(Mock()))
        self.assertEqual(self.rows()[0]['status'],'failed')
        self.assertEqual(self.rows()[0]['attempts'],3)


if __name__ == '__main__':
    unittest.main()
