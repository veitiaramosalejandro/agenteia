import unittest

from app.agent.schema_query_planner import (
    plan_identity_record_query,
    plan_related_record_query,
    plan_identity_relationship_query,
    plan_identity_relationship_queries,
    render_record_rows,
    render_relationship_rows,
)


CATALOG = {
    "tables": [
        {
            "schemaName": "dbo",
            "tableName": "SysCompany2Login",
            "columns": [
                {"name": "ID"}, {"name": "IDCompany"}, {"name": "IDLogin"},
            ],
            "foreignKeys": [
                {"column": "IDCompany", "referencedTable": "Entity", "referencedColumn": "ID"},
                {"column": "IDLogin", "referencedTable": "SysLogin", "referencedColumn": "IDLogin"},
            ],
        },
        {
            "schemaName": "dbo",
            "tableName": "Entity",
            "columns": [
                {"name": "ID"}, {"name": "accountname"}, {"name": "ShortName"},
                {"name": "isSystemCompany"},
            ],
            "foreignKeys": [],
        },
        {
            "schemaName": "dbo",
            "tableName": "SysResources",
            "columns": [
                {"name": "ResourceId"}, {"name": "DisplayName"}, {"name": "IDCompany"},
            ],
            "foreignKeys": [
                {"column": "IDCompany", "referencedTable": "Entity", "referencedColumn": "ID"},
            ],
        },
        {
            "schemaName": "dbo", "tableName": "SysLogin",
            "columns": [{"name": "IDLogin"}, {"name": "FullName"}], "foreignKeys": [],
        },
    ]
}

TASK_CATALOG = {
    "tables": [{
        "schemaName": "operations",
        "tableName": "SysTask",
        "columns": [
            {"name": "IDTask"}, {"name": "IDResource"},
            {"name": "IDResourceAssign"}, {"name": "ShortName"},
            {"name": "Code"}, {"name": "Description"},
            {"name": "TechnicalSpecification"},
            {"name": "WorkStatus"}, {"name": "ProgressPercentage"},
            {"name": "ModifiedTime"}, {"name": "Archived"},
            {"name": "DueDate"},
        ],
        "foreignKeys": [],
    }, {
        "schemaName": "operations",
        "tableName": "SysTaskResourceRole",
        "columns": [
            {"name": "IDTaskResourceRole"}, {"name": "IDTask"},
            {"name": "IDResource"}, {"name": "LinkState"},
        ],
        "foreignKeys": [
            {"column": "IDTask", "referencedTable": "SysTask", "referencedColumn": "IDTask"},
            {"column": "IDResource", "referencedTable": "SysResources", "referencedColumn": "ResourceId"},
        ],
    }]
}


class SchemaQueryPlannerTests(unittest.TestCase):
    def test_company_membership_follows_verified_foreign_key(self):
        login_id = "1790fc78-023d-4506-a7e8-5c030e9386d1"
        plan = plan_identity_relationship_query(
            "A que empresa pertenço no sistema?", CATALOG, login_id=login_id
        )

        self.assertIsNotNone(plan)
        self.assertEqual(("SysCompany2Login", "Entity"), plan.path)
        self.assertIn("anchor.[IDCompany] = target.[ID]", plan.query)
        self.assertIn("anchor.[IDLogin] = %s", plan.query)
        self.assertEqual([login_id], plan.parameters)
        self.assertNotIn(login_id, plan.query)

    def test_plan_requires_foreign_key_not_similar_names_only(self):
        catalog = {"tables": [dict(CATALOG["tables"][0], foreignKeys=[]), CATALOG["tables"][1]]}
        self.assertIsNone(plan_identity_relationship_query(
            "¿A qué empresa pertenezco?", catalog, login_id="login-1"
        ))

    def test_planner_offers_login_and_resource_fk_fallbacks(self):
        plans = plan_identity_relationship_queries(
            "A que empresa pertenço?", CATALOG,
            login_id="login-1", resource_id="resource-1",
        )
        self.assertEqual(
            {("SysCompany2Login", "Entity"), ("SysResources", "Entity")},
            {plan.path for plan in plans},
        )
        resource_plan = next(plan for plan in plans if plan.anchor_table == "SysResources")
        self.assertIn("anchor.[ResourceId] = %s", resource_plan.query)
        self.assertEqual(["resource-1"], resource_plan.parameters)

    def test_renderer_uses_only_returned_rows(self):
        plan = plan_identity_relationship_query(
            "Which company do I belong to?", CATALOG, login_id="login-1"
        )
        response = render_relationship_rows(
            [{"ShortName": "ISICOM", "accountname": "ISICOM Portugal"}], plan, "en"
        )
        self.assertEqual("In the system, you belong to: **ISICOM**.", response)

    def test_portuguese_renderer_is_explicit_and_natural(self):
        plan = plan_identity_relationship_query(
            "A que empresa pertenço?", CATALOG, login_id="login-1"
        )
        response = render_relationship_rows(
            [{"ShortName": "ROBOTEA"}], plan, "pt"
        )
        self.assertEqual(
            "A empresa à qual você pertence no sistema é **ROBOTEA**.", response
        )

    def test_current_task_in_spanish_is_planned_from_catalog(self):
        resource_id = "ce0e837a-fe28-47ae-9ba0-8841fe042ca8"
        plan = plan_identity_record_query(
            "¿Cuál es mi tarea actual en la que estoy trabajando?",
            TASK_CATALOG,
            resource_id=resource_id,
        )
        self.assertIsNotNone(plan)
        self.assertIn("FROM [operations].[SysTask]", plan.query)
        self.assertIn("src.[IDResource] = %s", plan.query)
        self.assertIn("src.[IDResourceAssign] = %s", plan.query)
        self.assertIn("[operations].[SysTaskResourceRole]", plan.query)
        self.assertIn("rel0.[IDResource] = %s", plan.query)
        self.assertIn("ISNULL(rel0.[LinkState], 1) <> 0", plan.query)
        self.assertIn("ISNULL(src.[WorkStatus], 0) <> 0", plan.query)
        self.assertEqual([resource_id, resource_id, resource_id], plan.parameters)
        self.assertNotIn(resource_id, plan.query)

    def test_latest_task_uses_relation_and_latest_renderer(self):
        plan = plan_identity_record_query(
            "¿Cuál fue tu última tarea en el sistema?",
            TASK_CATALOG,
            resource_id="victor-resource",
        )
        self.assertIsNotNone(plan)
        self.assertEqual("latest", plan.temporal_scope)
        self.assertIn("EXISTS (SELECT 1", plan.query)
        self.assertEqual(["victor-resource"] * 3, plan.parameters)
        response = render_record_rows(
            [{"ShortName": "Tarea de Victor", "ProgressPercentage": 90}], plan, "es"
        )
        self.assertIn("última tarea relacionada", response)

    def test_task_summary_builds_aggregate_query_and_data_driven_response(self):
        plan = plan_identity_record_query(
            "Dame un resumen del estado de tus tareas y cumplimiento",
            TASK_CATALOG,
            resource_id="victor-resource",
        )
        self.assertIsNotNone(plan)
        self.assertEqual("summary", plan.response_mode)
        self.assertIn("COUNT(*) AS [TaskCount]", plan.query)
        self.assertIn("AVG(CAST(src.[ProgressPercentage]", plan.query)
        self.assertNotIn("TOP 10", plan.query)
        self.assertEqual(["victor-resource"] * 3, plan.parameters)
        response = render_record_rows([{
            "TaskCount": 20,
            "CompletedCount": 5,
            "InProgressCount": 12,
            "NotStartedCount": 3,
            "AverageProgress": "62.50",
            "ActiveCount": 2,
        }], plan, "es")
        self.assertIn("**20 tareas**", response)
        self.assertIn("**62.50%**", response)
        self.assertIn("**25.00%**", response)

    def test_overdue_task_question_builds_verified_count(self):
        plan = plan_identity_record_query(
            "¿Cuántas tareas incumpliste?", TASK_CATALOG,
            resource_id="victor-resource",
        )
        self.assertIsNotNone(plan)
        self.assertEqual("overdue_count", plan.response_mode)
        self.assertIn("COUNT(*) AS [OverdueCount]", plan.query)
        self.assertIn("src.[DueDate] < GETDATE()", plan.query)
        self.assertIn("ISNULL(src.[ProgressPercentage], 0) < 100", plan.query)
        response = render_record_rows([{"OverdueCount": 7}], plan, "es")
        self.assertIn("**7 tareas", response)

    def test_current_task_in_portuguese_uses_same_safe_plan(self):
        plan = plan_identity_record_query(
            "Qual é a tarefa atual em que estou a trabalhar?",
            TASK_CATALOG,
            resource_id="resource-1",
        )
        self.assertIsNotNone(plan)
        self.assertEqual("SysTask", plan.table)
        self.assertIn("ProgressPercentage", plan.selected_columns)

    def test_current_task_renderer_uses_only_sql_row(self):
        plan = plan_identity_record_query(
            "Qual é a tarefa atual em que estou a trabalhar?",
            TASK_CATALOG,
            resource_id="resource-1",
        )
        response = render_record_rows([{
            "ShortName": "Implementação de formação melhorada.",
            "ProgressPercentage": 0,
            "WorkStatus": 1,
        }], plan, "pt")
        self.assertIn("**Implementação de formação melhorada.**", response)
        self.assertNotIn("currículo", response)

    def test_related_task_is_resolved_by_payload_code_and_catalog(self):
        plan = plan_related_record_query({
            "recordTypeName": "Task",
            "gidRecord": "60debe73-2ba2-f111-87af-ac162d7b04d3",
            "recordCode": "T-26-11369",
        }, TASK_CATALOG)
        self.assertIsNotNone(plan)
        self.assertIn("FROM [operations].[SysTask]", plan.query)
        self.assertIn("src.[Code] = %s", plan.query)
        self.assertIn("src.[IDTask] = %s", plan.query)
        self.assertEqual(
            ["T-26-11369", "60debe73-2ba2-f111-87af-ac162d7b04d3"],
            plan.parameters,
        )

    def test_invalid_related_gid_is_ignored_when_code_is_valid(self):
        plan = plan_related_record_query({
            "recordTypeName": "Task", "gidRecord": "0", "recordCode": "T-26-11246",
        }, TASK_CATALOG)
        self.assertIsNotNone(plan)
        self.assertEqual(["T-26-11246"], plan.parameters)
        self.assertNotIn("IDTask", plan.query)


if __name__ == "__main__":
    unittest.main()
