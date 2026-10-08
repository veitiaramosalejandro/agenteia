from app.services.suggestions import (
    _learned_assertion_response_prompt,
    _protect_learned_assertion_route,
    _sanitize_learned_assertion_output,
)


def test_learned_assertion_route_is_general_and_tool_free():
    metadata = {
        "model_capability": "coding",
        "tool_permissions": {"external_web", "query_sql_server"},
        "external_information_mode": True,
    }

    _protect_learned_assertion_route(metadata)

    assert metadata["model_capability"] == "general"
    assert metadata["tool_permissions"] == set()
    assert metadata["external_information_mode"] is False
    assert metadata["_capability_route_prepared"] is True


def test_learned_assertion_requests_an_acknowledgement_instead_of_an_echo():
    fact = (
        "En programación, Floyd se refiere a dos algoritmos.\n\n"
        "## Floyd-Warshall\n"
        "```python\n"
        "dist[i][j] = min(dist[i][j], dist[i][k] + dist[k][j])\n"
        "```"
    )

    prompt = _learned_assertion_response_prompt(fact, "es")

    assert "reconociendo brevemente" in prompt
    assert "No copies ni reformules todo el texto" in prompt
    assert "entre dos y cuatro formas concretas" in prompt
    assert prompt.endswith(fact)

    sanitized = _sanitize_learned_assertion_output(
        "Entendido.\n\n1. Ver un ejemplo.\n2. Implementarlo en Python."
    )
    assert "\n\n1. Ver" in sanitized


def test_learned_assertion_prompt_is_bounded_to_supplied_content():
    prompt = _learned_assertion_response_prompt(
        "Dijkstra no admite pesos negativos.", "es"
    )

    assert "No hagas búsquedas" in prompt
    assert "no introduzcas hechos externos" in prompt
