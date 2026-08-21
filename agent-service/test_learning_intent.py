import unittest

from app.main import (
    _is_informational_learning_message,
    _learning_acknowledgement,
    _looks_like_question_or_request,
)


class LearningIntentTests(unittest.TestCase):
    def test_portuguese_company_fact_is_learning(self):
        text = "ROBOTEA é o representante oficial da marca SolidSET, especializada em automação industrial."
        self.assertTrue(_is_informational_learning_message(text))

    def test_long_structured_information_is_learning(self):
        text = "Sobre Nós\n1991: início da atividade.\n1998: entrada no setor da robótica."
        self.assertTrue(_is_informational_learning_message(text))

    def test_portuguese_request_without_question_mark_is_not_learning_only(self):
        text = "Explica a história da ROBOTEA"
        self.assertTrue(_looks_like_question_or_request(text))
        self.assertFalse(_is_informational_learning_message(text))

    def test_spanish_request_without_question_mark_is_not_learning_only(self):
        self.assertFalse(_is_informational_learning_message("Necesito un resumen del proyecto"))

    def test_portuguese_acknowledgement_is_short_and_localized(self):
        self.assertEqual(
            "Agradeço a informação. Vou tê-la em conta.",
            _learning_acknowledgement("ROBOTEA é uma empresa de automação."),
        )


if __name__ == "__main__":
    unittest.main()
