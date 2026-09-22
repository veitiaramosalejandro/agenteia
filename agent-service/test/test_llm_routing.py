from app.llm.routing import requested_capability


def test_method_follow_up_routes_to_coding():
    assert requested_capability(
        "Y como podemos mejorar metodo AllTextBoxes_LostFocus?"
    ) == "coding"


def test_wpf_code_routes_to_coding():
    assert requested_capability("Revisa esta clase de C# y WPF") == "coding"


def test_algorithm_implementation_routes_to_coding():
    assert requested_capability(
        "Implementa el algoritmo Floyd en Java por favor?"
    ) == "coding"
