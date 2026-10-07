"""Real PostgreSQL trigger tests in isolated schemas, rolled back after each test."""
import unittest
from pathlib import Path
from uuid import uuid4
import psycopg

from app.connectors.db_client import _postgres_connection


class AgentDefaultModelTests(unittest.TestCase):
    def setUp(self):
        self.db = _postgres_connection()
        self.addCleanup(self.db.close)
        self.addCleanup(self.db.rollback)
        self.schema = "default_model_test_" + uuid4().hex
        self.db.execute("CREATE SCHEMA " + self.schema)
        self.db.execute(self.sql('CREATE TABLE public."SysResourceIA" ("IDResource" uuid PRIMARY KEY, "Name" text)'))
        root = Path(__file__).resolve().parents[1] / "app/connectors"
        self.db.execute(self.sql((root / "llm_schema.sql").read_text()))
        self.migration = self.sql((root / "agent_default_model.sql").read_text())
        self.local = self.provider("ollama-default", "ollama", True)
        self.remote = self.provider("custom-openai", "openai", False)
        self.db.execute(self.migration)

    def sql(self, value):
        return value.replace("public.", self.schema + ".").replace(
            "table_schema='public'", "table_schema='" + self.schema + "'"
        )

    def provider(self, code, provider, default):
        return self.db.execute(self.sql('''INSERT INTO public."SysLLMProviderConfiguration"
            ("Code","Name","Provider","Model","IsDefault") VALUES (%s,%s,%s,'test-model',%s)
            RETURNING "ID"'''), (code, code, provider, default)).fetchone()["ID"]

    def create_resource(self):
        resource = uuid4()
        self.db.execute(self.sql('INSERT INTO public."SysResourceIA" VALUES (%s,%s)'), (resource, "Twin"))
        return resource

    def models(self, resource):
        return self.db.execute(self.sql('SELECT * FROM public."SysAgentIAModel" WHERE "IDResource"=%s ORDER BY "IsDefault" DESC'), (resource,)).fetchall()

    def test_insert_assigns_one_local_default(self):
        rows = self.models(self.create_resource())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["IDProviderConfiguration"], self.local)
        self.assertTrue(rows[0]["IsDefault"])
        self.assertTrue(rows[0]["LocalExecution"])
        self.assertEqual(rows[0]["Capabilities"], ["general"])

    def test_sync_upsert_and_migration_are_idempotent(self):
        resource = self.create_resource()
        before = self.models(resource)
        for _ in range(2):
            self.db.execute(self.sql('''INSERT INTO public."SysResourceIA" VALUES (%s,'Updated')
                ON CONFLICT ("IDResource") DO UPDATE SET "Name"=EXCLUDED."Name"'''), (resource,))
            self.db.execute(self.migration)
        self.assertEqual(before, self.models(resource))

    def test_custom_default_survives_sync_and_backfill(self):
        resource = self.create_resource()
        self.db.execute(self.sql('UPDATE public."SysAgentIAModel" SET "IsDefault"=false WHERE "IDResource"=%s'), (resource,))
        self.db.execute(self.sql('''INSERT INTO public."SysAgentIAModel"
            ("IDResource","IDProviderConfiguration","IsDefault","LocalExecution","Priority")
            VALUES (%s,%s,true,false,7)'''), (resource, self.remote))
        before = self.models(resource)
        self.db.execute(self.sql('UPDATE public."SysResourceIA" SET "Name"=\'Synced\' WHERE "IDResource"=%s'), (resource,))
        self.db.execute(self.migration)
        self.assertEqual(before, self.models(resource))

    def test_backfill_adds_default_without_changing_specialist(self):
        resource = self.create_resource()
        self.db.execute(self.sql('DELETE FROM public."SysAgentIAModel" WHERE "IDResource"=%s'), (resource,))
        self.db.execute(self.sql('''INSERT INTO public."SysAgentIAModel"
            ("IDResource","IDProviderConfiguration","Capabilities","LocalExecution","Priority")
            VALUES (%s,%s,'["external_web"]',false,80)'''), (resource, self.remote))
        specialist = self.models(resource)[0]
        self.db.execute(self.migration)
        rows = self.models(resource)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["IDProviderConfiguration"], self.local)
        self.assertEqual(rows[1], specialist)

    def test_existing_local_assignment_is_promoted_without_losing_settings(self):
        resource = self.create_resource()
        self.db.execute(self.sql('''UPDATE public."SysAgentIAModel" SET "IsDefault"=false,
            "Priority"=9,"LearnFromOwner"=false WHERE "IDResource"=%s'''), (resource,))
        self.db.execute(self.migration)
        rows = self.models(resource)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["IsDefault"])
        self.assertEqual(rows[0]["Priority"], 9)
        self.assertFalse(rows[0]["LearnFromOwner"])

    def test_remote_global_default_does_not_change_new_twins_local_default(self):
        self.db.execute(self.sql('UPDATE public."SysLLMProviderConfiguration" SET "IsDefault"=false'))
        self.db.execute(self.sql('UPDATE public."SysLLMProviderConfiguration" SET "IsDefault"=true WHERE "ID"=%s'), (self.remote,))
        self.assertEqual(self.models(self.create_resource())[0]["IDProviderConfiguration"], self.local)

    def test_missing_local_provider_rejects_insert_atomically(self):
        self.db.execute(self.sql('UPDATE public."SysLLMProviderConfiguration" SET active=false WHERE "ID"=%s'), (self.local,))
        resource = uuid4()
        with self.assertRaises(psycopg.errors.RaiseException):
            with self.db.transaction():
                self.db.execute(self.sql('INSERT INTO public."SysResourceIA" VALUES (%s,\'Twin\')'), (resource,))
        self.assertEqual(self.db.execute(self.sql('SELECT count(*) AS n FROM public."SysResourceIA" WHERE "IDResource"=%s'), (resource,)).fetchone()["n"], 0)

    def test_inactive_assignment_history_is_preserved(self):
        resource = self.create_resource()
        self.db.execute(self.sql('UPDATE public."SysAgentIAModel" SET active=false WHERE "IDResource"=%s'), (resource,))
        old = self.models(resource)[0]
        self.db.execute(self.migration)
        rows = self.models(resource)
        self.assertEqual(len(rows), 2)
        self.assertIn(old, rows)
        self.assertEqual(sum(row["active"] and row["IsDefault"] for row in rows), 1)


if __name__ == "__main__":
    unittest.main()
