import unittest

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.prompts_maestro import SYSTEM_PROMPT_MAESTRO


class SystemPromptPolicyTests(unittest.TestCase):
    def test_current_turn_and_related_records_are_authoritative(self):
        self.assertIn("TURNO ACTUAL", SYSTEM_PROMPT)
        self.assertIn("RelatedRecordsData", SYSTEM_PROMPT)
        self.assertIn("tareas, actividades u otros registros", SYSTEM_PROMPT)

    def test_sql_requires_real_schema_fk_graph_and_string_parameters(self):
        self.assertIn("grafo real de claves", SYSTEM_PROMPT_MAESTRO)
        self.assertIn("foráneas", SYSTEM_PROMPT_MAESTRO)
        self.assertIn("parameters_json` debe enviarse como una CADENA", SYSTEM_PROMPT)
        self.assertIn("No inventes JOINs", SYSTEM_PROMPT)

    def test_prompt_blocks_topic_drift_and_unrequested_sql(self):
        self.assertIn("SQL no solicitado", SYSTEM_PROMPT_MAESTRO)
        self.assertIn("No confundas similitud con relevancia", SYSTEM_PROMPT_MAESTRO)
        self.assertIn("pregunta sobre `PWA`", SYSTEM_PROMPT)

    def test_suggestions_require_record_specific_evidence(self):
        self.assertIn("SUGERENCIAS Y CONSEJOS SOBRE REGISTROS", SYSTEM_PROMPT)
        self.assertIn("listas universales", SYSTEM_PROMPT)
        self.assertIn("una sola pregunta concreta", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
