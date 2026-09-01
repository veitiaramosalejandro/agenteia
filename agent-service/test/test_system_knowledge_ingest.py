import unittest
from app.system.system_knowledge_ingest import (
    _record_id,
    _related_resources,
    _safe_columns,
    _semantic_text,
    _keyset_where,
    _checkpoint_from_row,
    DEFAULT_BUSINESS_TABLES,
    RELATION_TABLES,
    RELATION_AGGREGATIONS,
    TABLE_PURPOSES,
)
from app.system.learning import SistemaAprendizaje


class TestSystemKnowledgeIngest(unittest.TestCase):
    def test_default_scope_uses_diagram_business_entities(self):
        self.assertIn("SysResources", DEFAULT_BUSINESS_TABLES)
        self.assertIn("SysTask", DEFAULT_BUSINESS_TABLES)
        self.assertIn("Activity", DEFAULT_BUSINESS_TABLES)
        self.assertIn("SysPerson", DEFAULT_BUSINESS_TABLES)
        self.assertIn("SysChat", DEFAULT_BUSINESS_TABLES)
        self.assertIn("Entity", DEFAULT_BUSINESS_TABLES)
        self.assertNotIn("Entity_SysCompany", DEFAULT_BUSINESS_TABLES)
        self.assertNotIn("Entity_SysPerson", DEFAULT_BUSINESS_TABLES)
        self.assertNotIn("SysFilesSystem", DEFAULT_BUSINESS_TABLES)

    def test_relation_tables_are_aggregated_not_individually_vectorized(self):
        self.assertIn("SysTaskResourceRole", RELATION_TABLES)
        self.assertIn("SysActivityResourceRoleActivity", RELATION_TABLES)
        self.assertIn("SysWorkRoomResource", RELATION_TABLES)
        self.assertIn("SysChat2SysResource", RELATION_TABLES)
        self.assertIn("SysChat2SysWorkRoom", RELATION_TABLES)
        self.assertIn("SysChat2Record", RELATION_TABLES)
        self.assertIn("SysCommunity2Company", RELATION_TABLES)
        self.assertIn("SysCommunity2Resource", RELATION_TABLES)
        self.assertIn("SysCommunity2WorkRoom", RELATION_TABLES)
        self.assertTrue(set(DEFAULT_BUSINESS_TABLES).isdisjoint(RELATION_TABLES))

    def test_company_community_resource_and_channel_graph_is_complete(self):
        entity_relations = {item["table"] for item in RELATION_AGGREGATIONS["Entity"]}
        community_relations = {item["table"] for item in RELATION_AGGREGATIONS["SysCommunity"]}
        resource_relations = {item["table"] for item in RELATION_AGGREGATIONS["SysResources"]}
        workroom_relations = {item["table"] for item in RELATION_AGGREGATIONS["SysWorkRoom"]}
        self.assertIn("SysCommunity2Company", entity_relations)
        self.assertIn("SysCommunity2Company", community_relations)
        self.assertIn("SysCommunity2Resource", resource_relations)
        self.assertIn("SysCommunity2WorkRoom", community_relations)
        self.assertIn("SysCommunity2WorkRoom", workroom_relations)
        self.assertIn("empresas y organizaciones", TABLE_PURPOSES["Entity"])

    def test_keyset_pagination_uses_primary_key_without_offset(self):
        where, parameters = _keyset_where(
            ["CreatedTime", "IDTask"],
            {"CreatedTime": "2026-08-31T10:00:00", "IDTask": "task-9"},
        )
        self.assertNotIn("OFFSET", where.upper())
        self.assertIn("[CreatedTime]>%s", where)
        self.assertIn("[CreatedTime]=%s AND [IDTask]>%s", where)
        self.assertEqual(
            ("2026-08-31T10:00:00", "2026-08-31T10:00:00", "task-9"),
            parameters,
        )

    def test_checkpoint_contains_only_ordering_key(self):
        checkpoint = _checkpoint_from_row(
            {"IDTask": "task-1", "Name": "ignored"}, ["IDTask"]
        )
        self.assertEqual({"IDTask": "task-1"}, checkpoint)

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

    def test_relation_summary_is_part_of_parent_semantic_document(self):
        text = _semantic_text(
            "SysTask", {"IDTask": "task-1", "ShortName": "Prueba"},
            "Relaciones SysTaskResourceRole (2): recursos asignados.",
        )
        self.assertIn("Relaciones SysTaskResourceRole (2)", text)

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
