"""Small deterministic text primitives used by explainable lexical metrics."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence

_TOKEN_RE = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)?", re.UNICODE)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")
_LINE_BOUNDARY_RE = re.compile(r"\r?\n+")
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s+")
_NUMBER_RE = re.compile(r"(?<![\w.])[+-]?\d+(?:[.,]\d+)*(?:%)?")
_URL_RE = re.compile(r"https?://[^\s<>{}\[\]\"']+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Normalize Unicode, case, punctuation, and whitespace for lexical comparison."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens = _TOKEN_RE.findall(normalized)
    return " ".join(tokens)


def tokenize(value: str) -> tuple[str, ...]:
    normalized = normalize_text(value)
    return tuple(normalized.split()) if normalized else ()


def token_f1(candidate: str, target: str) -> tuple[float, float, float]:
    """Return multiset token precision, recall, and F1."""

    candidate_tokens = Counter(tokenize(candidate))
    target_tokens = Counter(tokenize(target))
    if not candidate_tokens or not target_tokens:
        return (0.0, 0.0, 0.0)
    overlap = sum((candidate_tokens & target_tokens).values())
    precision = overlap / sum(candidate_tokens.values())
    recall = overlap / sum(target_tokens.values())
    if precision + recall == 0:
        return (precision, recall, 0.0)
    return (precision, recall, (2 * precision * recall) / (precision + recall))


def token_jaccard(candidate: str, target: str) -> float:
    candidate_tokens = set(tokenize(candidate))
    target_tokens = set(tokenize(target))
    union = candidate_tokens | target_tokens
    if not union:
        return 0.0
    return len(candidate_tokens & target_tokens) / len(union)


def split_sentences(value: str) -> tuple[str, ...]:
    """Split text into deterministic claim-sized units without external NLP models."""

    stripped = value.strip()
    if not stripped:
        return ()
    sentences: list[str] = []
    for line in _LINE_BOUNDARY_RE.split(stripped):
        collapsed = _WHITESPACE_RE.sub(" ", _BULLET_PREFIX_RE.sub("", line).strip())
        sentences.extend(
            part.strip() for part in _SENTENCE_BOUNDARY_RE.split(collapsed) if part.strip()
        )
    return tuple(sentences)


def extract_numbers(value: str) -> tuple[str, ...]:
    """Extract canonical numeric anchors in stable first-seen order."""

    seen: set[str] = set()
    anchors: list[str] = []
    for match in _NUMBER_RE.findall(unicodedata.normalize("NFKC", value)):
        anchor = match.replace(",", "").removesuffix("%")
        if anchor not in seen:
            seen.add(anchor)
            anchors.append(anchor)
    return tuple(anchors)


def extract_urls(value: str) -> tuple[str, ...]:
    """Extract URL anchors while excluding ordinary sentence punctuation."""

    seen: set[str] = set()
    anchors: list[str] = []
    for match in _URL_RE.findall(value):
        anchor = match.rstrip(".,;:!?)")
        if anchor not in seen:
            seen.add(anchor)
            anchors.append(anchor)
    return tuple(anchors)


def phrase_present(output: str, phrase: str) -> bool:
    """Match a phrase after the same Unicode/token normalization used by metrics."""

    normalized_phrase = normalize_text(phrase)
    return bool(normalized_phrase) and normalized_phrase in normalize_text(output)


def context_chunks(context: str | Sequence[str] | None) -> tuple[str, ...]:
    if context is None:
        return ()
    if isinstance(context, str):
        return (context,) if context.strip() else ()
    return tuple(chunk for chunk in context if chunk.strip())


def stable_unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return tuple(output)
