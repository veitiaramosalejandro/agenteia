"""Capability selection before any provider-specific shortcut."""
import re


def requested_capability(text: str, metadata: dict | None = None) -> str:
    metadata = metadata or {}
    explicit = str(metadata.get('model_capability') or '').strip().lower()
    if explicit:
        return explicit
    if metadata.get('response_suggestion_mode'):
        return 'general'
    text = str(text or '').casefold()
    if metadata.get('public_research') or re.search(
        r'\b(temperatura|temperature|weather|clima|meteorologia|meteorología|'
        r'pronóstico|previsão|noticias|notícias|news)\b|precio actual|cotización', text
    ):
        return 'external_web'
    if re.search(r'\b(sql|t-sql)\b', text):
        return 'sql'
    if re.search(r'\b(código|codigo|python|javascript|api|endpoint|docker|programar|programação)\b', text):
        return 'coding'
    if re.search(r'\b(analiza|analise|analisa|compara|razona|estrategia|estratégia|planifica)\b', text):
        return 'reasoning'
    return 'general'
