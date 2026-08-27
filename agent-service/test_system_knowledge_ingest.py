import unittest
from app.system.system_knowledge_ingest import (
    _record_id,
    _related_resources,
    _safe_columns,
    _semantic_text,
    DEFAULT_BUSINESS_TABLES,
)
from app.system.learning import SistemaAprendizaje


class TestSystemKnowledgeIngest(unittest.TestCase):
    def test_default_scope_uses_diagram_business_entities(self):
        self.assertIn("SysResources", DEFAULT_BUSINESS_TABLES)
        self.assertIn("SysTask", DEFAULT_BUSINESS_TABLES)
        self.assertIn("Activity", DEFAULT_BUSINESS_TABLES)
        self.assertNotIn("SysChat", DEFAULT_BUSINESS_TABLES)
        self.assertNotIn("SysFilesSystem", DEFAULT_BUSINESS_TABLES)

    def test_sensitive_and_binary_columns_are_excluded(self):
        columns = [
            {"COLUMN_NAME": "DisplayName", "DATA_TYPE": "nvarchar"},
            {"COLUMN_NAME": "Password", "DATA_TYPE": "nvarchar"},
            {"COLUMN_NAME": "Photo", "DATA_TYPE": "varbinary"},
            {"COLUMN_NAME": "ApiKey", "DATA_TYPE": "varchar"},
            {"COLUMN_NAME": "ModifiedTime", "DATA_TYPE": "datetime2"},
        ]
        self.assertEqual(["DisplayName", "ModifiedTime"], _safe_columns(columns))

    def test_row_becomes_natural_language_fact(self):
        text = _semantic_text("SysResources", {
            "DisplayName": "Alejandro Veitia",
            "IsCompanyOwner": True,
            "Active": True,
        })
        self.assertIn("recursos del sistema", text)
        self.assertIn("nombre visible: Alejandro Veitia", text)
        self.assertIn("es propietario de empresa: True", text)

    def test_related_resource_ids_are_extracted_for_authorization(self):
        resource = "272700d8-d1ba-46a6-a121-b76fce8ecb9f"
        self.assertEqual(
            [resource],
            _related_resources({"IDResource": resource, "IDTask": "not-a-resource"}),
        )

    def test_composite_primary_key_is_stable(self):
        row = {"IDTask": "task-1", "IDResource": "resource-1", "Name": "X"}
        self.assertEqual(
            "task-1|resource-1", _record_id(row, ["IDTask", "IDResource"])
        )

    def test_snapshot_retrieval_isolated_by_instance_and_resource(self):
        system = SistemaAprendizaje.__new__(SistemaAprendizaje)
        system._embed_query_safe = lambda *_args, **_kwargs: [0.1, 0.2]
        captured = {}

        def search(_vector, query_filter=None, **_kwargs):
            captured.update(query_filter or {})
            return [
                {"score": 0.9, "payload": {
                    "page_content": "CEO autorizado", "related_resource_ids": ["owner-1"]}},
                {"score": 0.95, "payload": {
                    "page_content": "Dato de otro recurso", "related_resource_ids": ["owner-2"]}},
            ]

        system._search_aprendizaje = search
        result = system.consultar_conocimiento_sistema(
            "quién es CEO", solidset_instance_id="instance-1",
            agent_resource_id="owner-1",
        )
        self.assertEqual("solidset_system_snapshot", captured["source"])
        self.assertEqual("instance-1", captured["solidset_instance_id"])
        self.assertIn("CEO autorizado", result)
        self.assertNotIn("otro recurso", result)


if __name__ == "__main__":
    unittest.main()
