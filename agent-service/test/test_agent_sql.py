import unittest
from unittest.mock import Mock

from app.agent.sql import AgentSql, reset_tool_permissions, set_tool_permissions


class AgentSqlTests(unittest.TestCase):
    def test_query_and_schema_preserve_arguments(self):
        query_tool = Mock()
        schema_tool = Mock()
        query_tool.invoke.return_value = "rows"
        schema_tool.invoke.return_value = "schema"
        sql = AgentSql(query_tool, schema_tool)
        query = {"query": "SELECT 1", "parameters_json": "[]"}
        schema = {"table_name": "SysTask"}

        self.assertEqual(sql.query(query), "rows")
        self.assertEqual(sql.schema(schema), "schema")
        query_tool.invoke.assert_called_once_with(query)
        schema_tool.invoke.assert_called_once_with(schema)

    def test_query_requires_sql_capability_when_request_scope_is_set(self):
        query_tool = Mock()
        schema_tool = Mock()
        sql = AgentSql(query_tool, schema_tool)
        token = set_tool_permissions({"solidset_schema"})
        try:
            with self.assertRaises(PermissionError):
                sql.query({"query": "SELECT 1"})
            sql.schema({"table_name": "SysTask"})
        finally:
            reset_tool_permissions(token)

    def test_permission_context_is_restored_after_reset(self):
        query_tool = Mock()
        sql = AgentSql(query_tool, Mock())
        token = set_tool_permissions(set())
        with self.assertRaises(PermissionError):
            sql.query({"query": "SELECT 1"})
        reset_tool_permissions(token)
        sql.query({"query": "SELECT 1"})


if __name__ == "__main__":
    unittest.main()
