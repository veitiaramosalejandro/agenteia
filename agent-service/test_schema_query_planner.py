import unittest

from app.agent.schema_query_planner import (
    plan_identity_relationship_query,
    plan_identity_relationship_queries,
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


if __name__ == "__main__":
    unittest.main()
