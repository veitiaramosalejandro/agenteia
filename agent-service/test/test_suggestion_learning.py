from app.services.suggestions import (
    _learned_assertion_output,
    _sanitize_learned_assertion_output,
)


def test_learned_assertion_preserves_markdown_and_code_as_one_suggestion():
    fact = (
        "En programación, Floyd se refiere a dos algoritmos.\n\n"
        "## Floyd-Warshall\n"
        "```python\n"
        "dist[i][j] = min(dist[i][j], dist[i][k] + dist[k][j])\n"
        "```"
    )

    raw, suggestions = _learned_assertion_output(fact)

    assert suggestions == [fact]
    assert "Floyd-Warshall" in raw

    sanitized = _sanitize_learned_assertion_output(suggestions[0])
    assert sanitized == fact
    assert "\n```python\n" in sanitized
