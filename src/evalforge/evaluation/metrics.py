"""Transparent, deterministic metrics for local LLM evaluation."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from typing import Any, Final

from jsonschema import SchemaError
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import Unresolvable

from evalforge.evaluation.text import (
    context_chunks,
    extract_numbers,
    extract_urls,
    normalize_text,
    phrase_present,
    split_sentences,
    token_f1,
    token_jaccard,
    tokenize,
)
from evalforge.evaluation.types import (
    EvaluationCase,
    MetricDirection,
    MetricResult,
    MetricStatus,
    OutputConstraints,
)

METRIC_VERSIONS: Final[Mapping[str, str]] = {
    "correctness": "lexical-correctness-v1",
    "relevance": "lexical-relevance-v1",
    "groundedness": "claim-support-v2",
    "hallucination_risk": "unsupported-anchor-risk-v2",
    "phrase_coverage": "normalized-phrase-coverage-v1",
    "json_validity": "json-schema-validity-v1",
    "constraint_adherence": "explicit-constraints-v2",
    "style_adherence": "deterministic-style-v2",
    "aggregate_quality": "direction-aware-weighted-v1",
}

_SUPPORTED_CLAIM_THRESHOLD: Final[float] = 0.72
_CONSECUTIVE_WORD_RE = re.compile(r"\b([\w'\u2019]+)\s+\1\b", re.IGNORECASE)
_BOILERPLATE_PHRASES: Final[tuple[str, ...]] = (
    "as an ai language model",
    "i cannot provide",
    "certainly",
)


def _clamp(score: float) -> float:
    return round(max(0.0, min(1.0, score)), 6)


def _not_applicable(
    name: str,
    reason: str,
    evidence: Mapping[str, Any],
    *,
    direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER,
) -> MetricResult:
    return MetricResult(
        name=name,
        version=METRIC_VERSIONS[name],
        score=None,
        status=MetricStatus.NOT_APPLICABLE,
        threshold=None,
        passed=None,
        reason=reason,
        evidence=evidence,
        direction=direction,
    )


def _applicable(
    name: str,
    score: float,
    *,
    threshold: float,
    reason: str,
    evidence: Mapping[str, Any],
    direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER,
) -> MetricResult:
    normalized_score = _clamp(score)
    if direction == MetricDirection.HIGHER_IS_BETTER:
        passed = normalized_score >= threshold
    else:
        passed = normalized_score <= threshold
    return MetricResult(
        name=name,
        version=METRIC_VERSIONS[name],
        score=normalized_score,
        status=MetricStatus.APPLICABLE,
        threshold=threshold,
        passed=passed,
        reason=reason,
        evidence=evidence,
        direction=direction,
    )


def _best_claim_support(claim: str, chunks: Sequence[str]) -> tuple[float, int | None]:
    best = 0.0
    best_index: int | None = None
    normalized_claim = normalize_text(claim)
    for index, chunk in enumerate(chunks):
        normalized_chunk = normalize_text(chunk)
        if normalized_claim and normalized_claim in normalized_chunk:
            return 1.0, index
        _precision, _recall, f1 = token_f1(claim, chunk)
        support = max(f1, token_jaccard(claim, chunk))
        if support > best:
            best = support
            best_index = index
    return best, best_index


def _unsupported_anchors(
    output: str,
    chunks: Sequence[str],
) -> tuple[list[str], list[str], list[str], list[str]]:
    evidence_text = "\n".join(chunks)
    output_numbers = list(extract_numbers(output))
    output_urls = list(extract_urls(output))
    context_numbers = set(extract_numbers(evidence_text))
    context_urls = set(extract_urls(evidence_text))
    unsupported_numbers = [value for value in output_numbers if value not in context_numbers]
    unsupported_urls = [value for value in output_urls if value not in context_urls]
    return output_numbers, output_urls, unsupported_numbers, unsupported_urls


class MetricRegistry:
    """Versioned metric implementations exposed through one stable facade."""

    @property
    def versions(self) -> Mapping[str, str]:
        return dict(METRIC_VERSIONS)

    def score_correctness(self, *, output: str, reference: str | None) -> MetricResult:
        if reference is None or not reference.strip():
            return _not_applicable(
                "correctness",
                "A non-empty reference answer is required for correctness.",
                {"reference_available": False},
            )
        normalized_output = normalize_text(output)
        normalized_reference = normalize_text(reference)
        exact_match = normalized_output == normalized_reference and bool(normalized_reference)
        precision, recall, token_score = token_f1(output, reference)
        sequence_score = SequenceMatcher(
            None,
            normalized_output,
            normalized_reference,
            autojunk=False,
        ).ratio()
        score = 1.0 if exact_match else (0.6 * token_score) + (0.4 * sequence_score)
        return _applicable(
            "correctness",
            score,
            threshold=0.7,
            reason="Compared normalized output with the stored reference answer.",
            evidence={
                "exact_match": exact_match,
                "token_precision": round(precision, 6),
                "token_recall": round(recall, 6),
                "token_f1": round(token_score, 6),
                "sequence_similarity": round(sequence_score, 6),
            },
        )

    def score_relevance(
        self,
        *,
        output: str,
        input_text: str,
        reference: str | None = None,
        relevance_keywords: Sequence[str] = (),
    ) -> MetricResult:
        configured_keywords = tuple(keyword for keyword in relevance_keywords if keyword.strip())
        if configured_keywords:
            target = " ".join(configured_keywords)
            target_source = "relevance_keywords"
            matched = [phrase for phrase in configured_keywords if phrase_present(output, phrase)]
            coverage = len(matched) / len(configured_keywords)
        elif reference is not None and reference.strip():
            target = reference
            target_source = "reference"
            matched = []
            coverage = token_jaccard(output, target)
        elif input_text.strip():
            target = input_text
            target_source = "input"
            matched = []
            coverage = token_jaccard(output, target)
        else:
            return _not_applicable(
                "relevance",
                "Relevance needs keywords, a reference answer, or an input question.",
                {"target_available": False},
            )
        precision, recall, lexical_f1 = token_f1(output, target)
        score = (0.7 * lexical_f1) + (0.3 * coverage)
        return _applicable(
            "relevance",
            score,
            threshold=0.55,
            reason=f"Compared output with the configured {target_source} relevance target.",
            evidence={
                "target_source": target_source,
                "token_precision": round(precision, 6),
                "token_recall": round(recall, 6),
                "token_f1": round(lexical_f1, 6),
                "coverage": round(coverage, 6),
                "matched_keywords": matched,
            },
        )

    def score_groundedness(
        self,
        *,
        output: str,
        context: str | Sequence[str] | None,
    ) -> MetricResult:
        chunks = context_chunks(context)
        if not chunks:
            return _not_applicable(
                "groundedness",
                "Groundedness requires at least one non-empty context chunk.",
                {"context_available": False},
            )
        claims = split_sentences(output)
        if not claims:
            return _applicable(
                "groundedness",
                0.0,
                threshold=0.65,
                reason="An empty output contains no context-supported claims.",
                evidence={
                    "claim_count": 0,
                    "claim_support": [],
                    "unsupported_numbers": [],
                    "unsupported_urls": [],
                },
            )
        _numbers, _urls, unsupported_numbers, unsupported_urls = _unsupported_anchors(
            output, chunks
        )
        context_number_set = set(extract_numbers("\n".join(chunks)))
        context_url_set = set(extract_urls("\n".join(chunks)))
        claim_scores: list[float] = []
        claim_evidence: list[dict[str, Any]] = []
        for index, claim in enumerate(claims):
            lexical_support, best_chunk_index = _best_claim_support(claim, chunks)
            claim_numbers = extract_numbers(claim)
            claim_urls = extract_urls(claim)
            anchor_count = len(claim_numbers) + len(claim_urls)
            supported_anchor_count = sum(
                number in context_number_set for number in claim_numbers
            ) + sum(url in context_url_set for url in claim_urls)
            anchor_ratio = supported_anchor_count / anchor_count if anchor_count else 1.0
            adjusted_support = lexical_support * anchor_ratio
            claim_scores.append(adjusted_support)
            claim_evidence.append(
                {
                    "claim_index": index,
                    "best_context_chunk_index": best_chunk_index,
                    "lexical_support": round(lexical_support, 6),
                    "anchor_support": round(anchor_ratio, 6),
                    "adjusted_support": round(adjusted_support, 6),
                }
            )
        score = sum(claim_scores) / len(claim_scores)
        return _applicable(
            "groundedness",
            score,
            threshold=0.65,
            reason="Averaged claim support against context and required factual-anchor support.",
            evidence={
                "claim_count": len(claims),
                "claim_support": claim_evidence,
                "unsupported_numbers": unsupported_numbers,
                "unsupported_urls": unsupported_urls,
            },
        )

    def score_hallucination_risk(
        self,
        *,
        output: str,
        context: str | Sequence[str] | None,
    ) -> MetricResult:
        chunks = context_chunks(context)
        if not chunks:
            return _not_applicable(
                "hallucination_risk",
                "Hallucination risk requires context against which to check claims.",
                {"context_available": False},
                direction=MetricDirection.LOWER_IS_BETTER,
            )
        claims = split_sentences(output)
        if not claims:
            return _applicable(
                "hallucination_risk",
                0.0,
                threshold=0.25,
                reason="An empty output introduces no unsupported factual claims.",
                evidence={
                    "claim_count": 0,
                    "unsupported_claim_indexes": [],
                    "unsupported_numbers": [],
                    "unsupported_urls": [],
                    "components": {
                        "unsupported_claim_ratio": 0.0,
                        "unsupported_number_ratio": 0.0,
                        "unsupported_url_ratio": 0.0,
                    },
                },
                direction=MetricDirection.LOWER_IS_BETTER,
            )
        output_numbers, output_urls, unsupported_numbers, unsupported_urls = _unsupported_anchors(
            output, chunks
        )
        claim_support_rows = [_best_claim_support(claim, chunks) for claim in claims]
        claim_support = [score for score, _chunk_index in claim_support_rows]
        unsupported_claim_indexes = [
            index
            for index, support in enumerate(claim_support)
            if support < _SUPPORTED_CLAIM_THRESHOLD
        ]
        unsupported_claim_ratio = len(unsupported_claim_indexes) / len(claims)
        unsupported_number_ratio = (
            len(unsupported_numbers) / len(output_numbers) if output_numbers else 0.0
        )
        unsupported_url_ratio = len(unsupported_urls) / len(output_urls) if output_urls else 0.0
        risk = (
            (0.45 * unsupported_claim_ratio)
            + (0.35 * unsupported_number_ratio)
            + (0.20 * unsupported_url_ratio)
        )
        return _applicable(
            "hallucination_risk",
            risk,
            threshold=0.25,
            reason=(
                "Combined unsupported claim frequency with separate numeric and URL anchor risk."
            ),
            evidence={
                "claim_count": len(claims),
                "claim_support": [round(value, 6) for value in claim_support],
                "unsupported_claim_indexes": unsupported_claim_indexes,
                "best_context_chunk_indexes": [
                    chunk_index for _score, chunk_index in claim_support_rows
                ],
                "unsupported_numbers": unsupported_numbers,
                "unsupported_urls": unsupported_urls,
                "components": {
                    "unsupported_claim_ratio": round(unsupported_claim_ratio, 6),
                    "unsupported_number_ratio": round(unsupported_number_ratio, 6),
                    "unsupported_url_ratio": round(unsupported_url_ratio, 6),
                },
            },
            direction=MetricDirection.LOWER_IS_BETTER,
        )

    def score_phrase_coverage(
        self,
        *,
        output: str,
        required_phrases: Sequence[str],
    ) -> MetricResult:
        phrases = tuple(phrase for phrase in required_phrases if phrase.strip())
        if not phrases:
            return _not_applicable(
                "phrase_coverage",
                "No required phrases were configured.",
                {"required_phrase_count": 0},
            )
        matched = [phrase for phrase in phrases if phrase_present(output, phrase)]
        missing = [phrase for phrase in phrases if phrase not in matched]
        score = len(matched) / len(phrases)
        return _applicable(
            "phrase_coverage",
            score,
            threshold=1.0,
            reason="Measured normalized coverage of every configured required phrase.",
            evidence={
                "required_phrase_count": len(phrases),
                "matched_phrases": matched,
                "missing_phrases": missing,
            },
        )

    def score_json_validity(
        self,
        *,
        output: str,
        required: bool = False,
        schema: Mapping[str, Any] | None = None,
    ) -> MetricResult:
        if not required and schema is None:
            return _not_applicable(
                "json_validity",
                "JSON output was not required for this test case.",
                {"json_required": False, "schema_configured": False},
            )
        try:
            parsed = json.loads(output)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _applicable(
                "json_validity",
                0.0,
                threshold=1.0,
                reason="The output is not syntactically valid JSON.",
                evidence={
                    "json_valid": False,
                    "schema_configured": schema is not None,
                    "schema_valid": False if schema is not None else None,
                    "error_category": "json_syntax",
                },
            )
        if schema is None:
            return _applicable(
                "json_validity",
                1.0,
                threshold=1.0,
                reason="The output parsed as JSON.",
                evidence={
                    "json_valid": True,
                    "schema_configured": False,
                    "schema_valid": None,
                },
            )
        validator_type = validator_for(schema)
        try:
            validator_type.check_schema(schema)
        except SchemaError:
            return MetricResult(
                name="json_validity",
                version=METRIC_VERSIONS["json_validity"],
                score=None,
                status=MetricStatus.ERROR,
                threshold=None,
                passed=None,
                reason="The configured JSON schema is invalid.",
                evidence={"error_category": "invalid_schema"},
            )
        try:
            validation_errors = sorted(
                validator_type(schema, registry=Registry()).iter_errors(parsed),
                key=lambda error: tuple(str(part) for part in error.absolute_path),
            )
        except Unresolvable:
            return MetricResult(
                name="json_validity",
                version=METRIC_VERSIONS["json_validity"],
                score=None,
                status=MetricStatus.ERROR,
                threshold=None,
                passed=None,
                reason="The configured JSON schema references an unavailable external resource.",
                evidence={"error_category": "external_schema_reference"},
            )
        if validation_errors:
            first_error = validation_errors[0]
            return _applicable(
                "json_validity",
                0.0,
                threshold=1.0,
                reason="The output is valid JSON but does not satisfy the configured schema.",
                evidence={
                    "json_valid": True,
                    "schema_configured": True,
                    "schema_valid": False,
                    "error_category": "schema_validation",
                    "validator": str(first_error.validator),
                    "path": [str(part) for part in first_error.absolute_path],
                    "error_count": len(validation_errors),
                },
            )
        return _applicable(
            "json_validity",
            1.0,
            threshold=1.0,
            reason="The output is valid JSON and satisfies the configured schema.",
            evidence={
                "json_valid": True,
                "schema_configured": True,
                "schema_valid": True,
                "error_count": 0,
            },
        )

    def score_constraint_adherence(
        self,
        *,
        output: str,
        constraints: OutputConstraints,
    ) -> MetricResult:
        if not constraints.configured:
            return _not_applicable(
                "constraint_adherence",
                "No explicit output constraints were configured.",
                {"configured_rule_count": 0},
            )
        word_count = len(tokenize(output))
        sentence_count = len(split_sentences(output))
        checks: list[tuple[str, bool]] = []
        if constraints.min_words is not None:
            checks.append(("min_words", word_count >= constraints.min_words))
        if constraints.max_words is not None:
            checks.append(("max_words", word_count <= constraints.max_words))
        if constraints.min_sentences is not None:
            checks.append(("min_sentences", sentence_count >= constraints.min_sentences))
        if constraints.max_sentences is not None:
            checks.append(("max_sentences", sentence_count <= constraints.max_sentences))
        if constraints.required_prefix is not None:
            checks.append(("required_prefix", output.startswith(constraints.required_prefix)))
        if constraints.required_suffix is not None:
            checks.append(("required_suffix", output.endswith(constraints.required_suffix)))
        checks.extend(
            (
                f"forbidden_phrase:{phrase}",
                not phrase_present(output, phrase),
            )
            for phrase in constraints.forbidden_phrases
            if phrase.strip()
        )
        violations = [name for name, passed in checks if not passed]
        score = sum(passed for _name, passed in checks) / len(checks)
        return _applicable(
            "constraint_adherence",
            score,
            threshold=1.0,
            reason="Scored every explicitly configured deterministic output constraint.",
            evidence={
                "configured_rule_count": len(checks),
                "word_count": word_count,
                "sentence_count": sentence_count,
                "violations": violations,
                "checks": {name: passed for name, passed in checks},
            },
        )

    def score_style_adherence(self, *, output: str) -> MetricResult:
        sentences = split_sentences(output)
        sentence_lengths = [len(tokenize(sentence)) for sentence in sentences]
        average_sentence_words = (
            sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0.0
        )
        normalized = normalize_text(output)
        checks = [
            ("non_empty", bool(normalized)),
            ("trimmed_whitespace", output == output.strip()),
            ("average_sentence_length", average_sentence_words <= 30),
            ("maximum_sentence_length", max(sentence_lengths, default=0) <= 45),
            (
                "boilerplate",
                not any(phrase in normalized for phrase in _BOILERPLATE_PHRASES),
            ),
            ("repeated_consecutive_word", _CONSECUTIVE_WORD_RE.search(output) is None),
        ]
        violations = [name for name, passed in checks if not passed]
        score = sum(passed for _name, passed in checks) / len(checks)
        return _applicable(
            "style_adherence",
            score,
            threshold=0.8,
            reason="Applied a fixed, documented set of readability and boilerplate checks.",
            evidence={
                "sentence_count": len(sentences),
                "average_sentence_words": round(average_sentence_words, 6),
                "maximum_sentence_words": max(sentence_lengths, default=0),
                "violations": violations,
                "checks": {name: passed for name, passed in checks},
            },
        )

    def evaluate(self, case: EvaluationCase) -> tuple[MetricResult, ...]:
        """Evaluate every built-in metric in stable display order."""

        return (
            self.score_correctness(output=case.output, reference=case.reference),
            self.score_relevance(
                output=case.output,
                input_text=case.input_text,
                reference=case.reference,
                relevance_keywords=case.relevance_keywords,
            ),
            self.score_groundedness(output=case.output, context=case.context),
            self.score_hallucination_risk(output=case.output, context=case.context),
            self.score_phrase_coverage(
                output=case.output,
                required_phrases=case.required_phrases,
            ),
            self.score_json_validity(
                output=case.output,
                required=case.expects_json,
                schema=case.json_schema,
            ),
            self.score_constraint_adherence(
                output=case.output,
                constraints=case.constraints,
            ),
            self.score_style_adherence(output=case.output),
        )


def aggregate_metric_results(
    results: Sequence[MetricResult],
    *,
    weights: Mapping[str, float] | None = None,
) -> MetricResult:
    """Aggregate applicable metrics after orienting lower-is-better scores once."""

    configured_weights = dict(weights or {})
    invalid_weights = {
        name: weight
        for name, weight in configured_weights.items()
        if not isinstance(weight, (int, float))
        or isinstance(weight, bool)
        or not math.isfinite(float(weight))
        or float(weight) < 0
    }
    if invalid_weights:
        raise ValueError("Metric weights must be finite non-negative numbers")
    seen: set[str] = set()
    included: list[str] = []
    excluded: list[str] = []
    oriented_scores: dict[str, float] = {}
    contributions: dict[str, float] = {}
    denominator = 0.0
    numerator = 0.0
    for result in results:
        if result.name in seen:
            raise ValueError(f"Duplicate metric result: {result.name}")
        seen.add(result.name)
        weight = float(configured_weights.get(result.name, 1.0))
        if not result.applicable or result.score is None or weight == 0.0:
            excluded.append(result.name)
            continue
        oriented = (
            1.0 - result.score
            if result.direction == MetricDirection.LOWER_IS_BETTER
            else result.score
        )
        contribution = oriented * weight
        included.append(result.name)
        oriented_scores[result.name] = _clamp(oriented)
        contributions[result.name] = round(contribution, 6)
        numerator += contribution
        denominator += weight
    evidence = {
        "included_metrics": included,
        "excluded_metrics": excluded,
        "effective_denominator": round(denominator, 6),
        "oriented_scores": oriented_scores,
        "weighted_contributions": contributions,
    }
    if denominator == 0.0:
        return _not_applicable(
            "aggregate_quality",
            "No applicable metric with a positive weight was available.",
            evidence,
        )
    return _applicable(
        "aggregate_quality",
        numerator / denominator,
        threshold=0.7,
        reason="Weighted only applicable metrics after direction normalization.",
        evidence=evidence,
    )
