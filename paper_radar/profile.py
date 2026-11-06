"""A tiny, dependency-free keyword profile.

The model is deliberately simple:

* tokenize title + abstract;
* add ``+1`` for a like and ``-1`` for a skip, scaled by a 60-day half-life;
* score a paper by the sum of its keyword weights divided by ``sqrt(n)``.

No embeddings, no sklearn, no hidden magic.  That keeps the recommendation
explainable and makes every result reproducible.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

HALF_LIFE_DAYS = 60.0
RATING_WEIGHT = 1.0

# Deliberately small: domain words such as "retrieval", "benchmark", "model",
# and "language" must survive.  Only glue words and generic paper boilerplate
# are removed.
STOPWORDS = frozenset(
    {
        "a", "about", "above", "after", "again", "against", "all", "also",
        "am", "an", "and", "any", "are", "as", "at", "be", "because", "been",
        "before", "being", "below", "between", "both", "but", "by", "can",
        "could", "did", "do", "does", "doing", "down", "during", "each",
        "few", "for", "from", "further", "had", "has", "have", "having",
        "he", "her", "here", "hers", "herself", "him", "himself", "his",
        "how", "however", "i", "if", "in", "into", "is", "it", "its",
        "itself", "just", "me", "more", "most", "my", "myself", "no", "nor",
        "not", "now", "of", "off", "on", "once", "only", "or", "other",
        "our", "ours", "ourselves", "out", "over", "own", "paper", "per",
        "same", "she", "should", "so", "some", "such", "than", "that",
        "the", "their", "theirs", "them", "themselves", "then", "there",
        "these", "they", "this", "those", "through", "to", "too", "under",
        "until", "up", "very", "via", "was", "we", "were", "what", "when",
        "where", "which", "while", "who", "whom", "why", "will", "with",
        "within", "without", "would", "you", "your", "yours", "yourself",
        "yourselves", "propose", "proposed", "approach", "method", "methods",
        "result", "results", "show", "shows", "shown", "use", "used", "uses",
        "using", "based", "new", "novel", "toward", "towards", "well",
    }
)

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str | None) -> list[str]:
    """Lowercase, split on non-alphanumerics, and drop noise words."""

    if not text:
        return []
    tokens = _TOKEN_RE.findall(text.lower())
    return [token for token in tokens if len(token) >= 3 and token not in STOPWORDS]


def paper_words(paper: Mapping[str, Any]) -> set[str]:
    """Unique content words for one paper."""

    text = f"{paper.get('title') or ''} {paper.get('summary') or ''}"
    return set(tokenize(text))


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decay(created_at: Any, now: datetime) -> float:
    """Exponential decay with a 60-day half-life."""

    rated_at = _parse_datetime(created_at)
    if rated_at is None:
        return 1.0
    age_days = max(0.0, (now - rated_at).total_seconds() / 86400.0)
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def _iter_ratings(
    ratings: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None,
) -> Iterable[tuple[str, str, Any]]:
    if not ratings:
        return
    if isinstance(ratings, Mapping):
        for paper_id, payload in ratings.items():
            if isinstance(payload, Mapping):
                yield str(paper_id), str(payload.get("action") or ""), payload.get(
                    "created_at"
                )
            elif isinstance(payload, str):
                yield str(paper_id), payload, None
        return
    for item in ratings:
        if not isinstance(item, Mapping):
            continue
        paper_id = item.get("paper_id") or item.get("id")
        if paper_id:
            yield str(paper_id), str(item.get("action") or ""), item.get("created_at")


def build_profile(
    papers: Iterable[Mapping[str, Any]],
    ratings: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None,
) -> dict[str, float]:
    """Build keyword weights from rated papers.

    Each rating contributes one unit per unique word in the paper, multiplied by
    the time-decay factor.  A like adds weight; a skip subtracts it.
    """

    by_id = {
        str(paper.get("id")): paper
        for paper in papers
        if isinstance(paper, Mapping) and paper.get("id")
    }
    now = datetime.now(timezone.utc)
    weights: dict[str, float] = {}

    for paper_id, action, created_at in _iter_ratings(ratings):
        if action not in {"like", "skip"}:
            continue
        paper = by_id.get(paper_id)
        if paper is None:
            continue
        sign = 1.0 if action == "like" else -1.0
        contribution = sign * RATING_WEIGHT * _decay(created_at, now)
        for word in paper_words(paper):
            weights[word] = weights.get(word, 0.0) + contribution

    # Drop words whose contributions cancelled out exactly (or almost exactly).
    return {word: weight for word, weight in weights.items() if abs(weight) > 1e-12}


def score_paper(profile: Mapping[str, float], paper: Mapping[str, Any]) -> float:
    """Score a paper with the profile; returns ``0.0`` for an empty paper."""

    words = paper_words(paper)
    if not words:
        return 0.0
    total = sum(float(profile.get(word, 0.0)) for word in words)
    return total / math.sqrt(len(words))
