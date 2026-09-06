"""Integration test in a disposable PostgreSQL schema, always rolled back."""
import unittest
from pathlib import Path
from uuid import uuid4
from app.connectors.db_client import _postgres_connection

class LLMSchemaMigrationTests(unittest.TestCase):
    def test_legacy_links_move_without_changing_existing_assignments_or_secrets(self):
        schema = "llm_test_" + uuid4().hex
        script = (Path(__file__).resolve().parents[1] / "app/connectors/llm_schema.sql").read_text()
        script = script.replace("public.", schema + ".").replace("table_schema='public'", "table_schema='" + schema + "'")
        db = _postgres_connection()
        try:
            db.execute("CREATE SCHEMA " + schema)
            db.execute('CREATE TABLE '+schema+'."SysResourceIA" ("IDResource" uuid PRIMARY KEY)')
            db.execute(script)
            db.execute('ALTER TABLE '+schema+'."SysLLMProviderConfiguration" ADD COLUMN "IDResource" uuid')
            r1, r2, p1, p2 = [uuid4() for _ in range(4)]
            db.execute('INSERT INTO '+schema+'."SysResourceIA" VALUES (%s),(%s)', (r1,r2))
            for resource, provider, code in [(r1,p1,"legacy"),(r2,p2,"existing")]:
                db.execute('INSERT INTO '+schema+'."SysLLMProviderConfiguration" ("ID","Code","Name","Provider","Model","APIKey","IDResource") VALUES (%s,%s,%s,%s,%s,%s,%s)',(provider,code,code,'openai','test','encrypted-test-value',resource))
            db.execute('INSERT INTO '+schema+'."SysAgentIAModel" ("IDResource","IDProviderConfiguration","Capabilities","Priority","LearnFromOwner") VALUES (%s,%s,%s,7,false)',(r2,p2,'["external_web"]'))
            db.execute(script)
            db.execute(script)
            count=db.execute("SELECT count(*) AS n FROM information_schema.columns WHERE table_schema=%s AND table_name='SysLLMProviderConfiguration' AND column_name='IDResource'",(schema,)).fetchone()
            self.assertEqual(count['n'],0)
            rows=db.execute('SELECT * FROM '+schema+'."SysAgentIAModel" ORDER BY "Priority"').fetchall()
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['Capabilities'],['external_web'])
            self.assertFalse(rows[0]['LearnFromOwner'])
            self.assertEqual(rows[1]['IDResource'],r1)
            self.assertTrue(rows[1]['IsDefault'])
            self.assertFalse(rows[1]['LocalExecution'])
            keys=db.execute('SELECT "APIKey" FROM '+schema+'."SysLLMProviderConfiguration"').fetchall()
            self.assertEqual([r['APIKey'] for r in keys],['encrypted-test-value']*2)
        finally:
            db.rollback()
            db.close()
