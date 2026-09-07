import unittest
from unittest.mock import Mock

from app.agent.sql import AgentSql


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


if __name__ == "__main__":
    unittest.main()
