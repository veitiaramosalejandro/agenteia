"""Statistical language resolution with conversation-aware fallbacks."""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from typing import Optional

import redis
import langid

from app.config import settings

try:
    from lingua import LanguageDetectorBuilder
except ImportError:  # Allows startup while an old image is being upgraded.
    LanguageDetectorBuilder = None


@dataclass(frozen=True)
class LanguageDecision:
    language: str
    confidence: float
    source: str


class LanguageResolver:
    """Resolve language without vocabulary lists or an extra LLM request."""

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self._detector = (
            LanguageDetectorBuilder.from_all_languages().build()
            if LanguageDetectorBuilder is not None
            else None
        )
        self._redis = redis.Redis.from_url(
            redis_url or settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )

    @staticmethod
    def normalize_language(value: object) -> str:
        raw = str(value or "").strip().replace("_", "-").lower()
        code = raw.split("-", 1)[0]
        return code if re.fullmatch(r"[a-z]{2,3}", code) else ""

    def detect(self, text: str) -> LanguageDecision:
        clean = " ".join(str(text or "").split())
        if not clean or self._detector is None:
            return LanguageDecision("", 0.0, "undetermined")
        try:
            values = self._detector.compute_language_confidence_values(clean)
            if not values:
                return LanguageDecision("", 0.0, "undetermined")
            best = values[0]
            iso = getattr(best.language, "iso_code_639_1", None)
            lingua_language = self.normalize_language(getattr(iso, "name", iso))
            lingua_value = float(best.value)
            lingua_second = float(values[1].value) if len(values) > 1 else 0.0
            lingua_ratio = lingua_value / max(lingua_second, 1e-9)

            ranked = langid.rank(clean)
            langid_language = self.normalize_language(ranked[0][0]) if ranked else ""
            langid_margin = (
                float(ranked[0][1]) - float(ranked[1][1])
                if len(ranked) > 1 else 0.0
            )

            langid_strong = langid_margin >= 3.0
            lingua_strong = lingua_ratio >= 3.0
            if lingua_language and lingua_language == langid_language:
                language = lingua_language
                confidence = max(
                    0.90,
                    lingua_value,
                    1.0 - math.exp(-max(0.0, langid_margin)),
                )
            elif langid_strong and lingua_strong:
                # Two confident classifiers disagree: do not guess. Locale or
                # conversation memory is safer than choosing one arbitrarily.
                language = langid_language or lingua_language
                confidence = 0.49
            elif langid_language and langid_strong:
                language = langid_language
                confidence = 0.90
            elif lingua_language and lingua_strong:
                language = lingua_language
                confidence = 0.85
            else:
                # Preserve the statistical candidate for observability, but
                # deliberately force the conversation/locale fallback.
                language = langid_language or lingua_language
                confidence = min(lingua_value, 0.49)
            # A single token/proper name has no dependable grammatical signal,
            # even when a classifier reports a deceptively high score.
            word_count = len(re.findall(r"[^\W\d_]+", clean, flags=re.UNICODE))
            if word_count < 2:
                confidence = min(confidence, 0.49)
            return LanguageDecision(language, confidence, "statistical_ensemble")
        except Exception:
            return LanguageDecision("", 0.0, "undetermined")

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"solidset:conversation-language:v1:{session_id}"

    def _remembered(self, session_id: str) -> str:
        if not session_id:
            return ""
        try:
            return self.normalize_language(self._redis.get(self._session_key(session_id)))
        except redis.RedisError:
            return ""

    def _remember(self, session_id: str, language: str) -> None:
        if not session_id or not language:
            return
        try:
            key = self._session_key(session_id)
            if hasattr(self._redis, "set"):
                self._redis.set(
                    key,
                    language,
                    ex=settings.LANGUAGE_SESSION_TTL_SECONDS,
                )
            else:  # Compatibility with minimal Redis adapters used by clients/tests.
                self._redis.setex(
                    key,
                    settings.LANGUAGE_SESSION_TTL_SECONDS,
                    language,
                )
        except redis.RedisError:
            pass

    def resolve(
        self,
        text: str,
        *,
        session_id: str = "",
        locale: str = "",
        preferred_language: str = "",
        default_language: str = "",
        remember: bool = True,
    ) -> LanguageDecision:
        detected = self.detect(text)
        if detected.language and detected.confidence >= settings.LANGUAGE_MIN_CONFIDENCE:
            if remember:
                self._remember(session_id, detected.language)
            return detected

        candidates = (
            (self._remembered(session_id), "conversation"),
            (self.normalize_language(locale), "locale"),
            (self.normalize_language(preferred_language), "resource_preference"),
            (
                self.normalize_language(default_language or settings.LANGUAGE_DEFAULT),
                "instance_default",
            ),
            (detected.language, "low_confidence_statistical"),
            ("pt", "system_default"),
        )
        language, source = next(
            (value, source) for value, source in candidates if value
        )
        return LanguageDecision(language, detected.confidence, source)
