import unittest

from app.main import _quoted_reply_is_learning_only


class QuotedReplyIntentTests(unittest.TestCase):
    def test_long_factual_correction_is_learning_only(self):
        candidate = {
            "message": "A ROBOTEA é o parceiro oficial da marca SolidSET e foi criada pelo Grupo ISICOM.",
            "quoted_message": "Robotea poderá estar relacionada com robótica avançada.",
        }
        self.assertTrue(_quoted_reply_is_learning_only(candidate))

    def test_question_about_quoted_message_requires_response(self):
        candidate = {
            "message": "Esta informação sobre a ROBOTEA está correta?",
            "quoted_message": "Robotea poderá estar relacionada com robótica avançada.",
        }
        self.assertFalse(_quoted_reply_is_learning_only(candidate))

    def test_request_without_question_mark_requires_response(self):
        candidate = {
            "message": "Explica a diferença entre estas empresas",
            "quoted_message": "ROBOTEA, CADNEA e X3D fazem parte da nova estrutura.",
        }
        self.assertFalse(_quoted_reply_is_learning_only(candidate))

    def test_short_continuation_requires_response(self):
        candidate = {"message": "Sim", "quoted_message": "Posso continuar?"}
        self.assertFalse(_quoted_reply_is_learning_only(candidate))


if __name__ == "__main__":
    unittest.main()
