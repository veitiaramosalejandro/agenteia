import unittest

from app.security import validate_read_query


class ReadQuerySecurityTests(unittest.TestCase):
    def test_accepts_parameterized_select_and_cte(self):
        self.assertEqual("SELECT TOP (%s) ID FROM dbo.SysChat", validate_read_query(
            "SELECT TOP (%s) ID FROM dbo.SysChat"
        ))
        self.assertTrue(validate_read_query("WITH items AS (SELECT 1 AS ID) SELECT ID FROM items"))

    def test_rejects_writes_comments_and_multiple_statements(self):
        for query in (
            "UPDATE dbo.SysChat SET RawMessage='x'",
            "SELECT 1; DELETE FROM dbo.SysChat",
            "SELECT 1 -- hidden instruction",
            "EXEC dbo.SomeProcedure",
        ):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    validate_read_query(query)

    def test_rejects_cross_database_and_control_statements(self):
        for query in (
            "SELECT * FROM OtherDatabase.dbo.SysChat",
            "WAITFOR DELAY '00:00:05'; SELECT 1",
            "DECLARE @value int SELECT @value",
        ):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    validate_read_query(query)

    def test_accepts_schema_table_and_qualified_alias_columns(self):
        query = "SELECT dboTable.ID FROM dbo.SysMeeting dboTable WHERE dboTable.ID=%s"
        self.assertEqual(query, validate_read_query(query))


if __name__ == "__main__":
    unittest.main()
