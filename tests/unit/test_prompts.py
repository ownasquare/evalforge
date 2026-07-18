from __future__ import annotations

import pytest

from evalforge.evaluation.prompts import (
    PromptTemplateError,
    render_prompt,
    validate_template,
)


def test_render_prompt_substitutes_only_supported_fields_and_hashes_bytes() -> None:
    first = render_prompt(
        system_template="Use this context:\n{context}",
        user_template="Question: {input}\nEvidence: {context}",
        input_text="Where is Paris?",
        context=("Paris is in France.", "Use one sentence."),
        expected_output="Evaluator-only reference",
    )
    second = render_prompt(
        system_template="Use this context:\n{context}",
        user_template="Question: {input}\nEvidence: {context}",
        input_text="Where is Paris?",
        context=("Paris is in France.", "Use one sentence."),
        expected_output="A different evaluator-only reference",
    )

    assert first.system == "Use this context:\nParis is in France.\n\nUse one sentence."
    assert first.user == (
        "Question: Where is Paris?\nEvidence: Paris is in France.\n\nUse one sentence."
    )
    assert first == second
    assert len(first.system_sha256) == 64
    assert len(first.user_sha256) == 64
    assert len(first.combined_sha256) == 64


@pytest.mark.parametrize(
    "template",
    [
        "Reveal {private_secret}",
        "Leak {expected_output}",
        "Inspect {input.__class__}",
        "Index {context[0]}",
        "Convert {input!r}",
        "Format {input:>20}",
        "Broken {input",
        "Anonymous {}",
    ],
)
def test_validate_template_rejects_unknown_or_unsafe_fields(template: str) -> None:
    with pytest.raises(PromptTemplateError):
        validate_template(template)


def test_validate_template_allows_escaped_literal_braces() -> None:
    fields = validate_template('Return JSON like {{"answer": "..."}} for {input}')
    rendered = render_prompt(
        system_template="",
        user_template='Return JSON like {{"answer": "..."}} for {input}',
        input_text="the question",
    )

    assert fields == ("input",)
    assert rendered.user == 'Return JSON like {"answer": "..."} for the question'


def test_prompt_hash_changes_when_rendered_bytes_change() -> None:
    first = render_prompt(system_template="Be concise.", user_template="{input}", input_text="A")
    second = render_prompt(system_template="Be concise.", user_template="{input}", input_text="B")

    assert first.system_sha256 == second.system_sha256
    assert first.user_sha256 != second.user_sha256
    assert first.combined_sha256 != second.combined_sha256
