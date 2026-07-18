"""Strict prompt rendering and byte-level provenance hashes."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from string import Formatter
from typing import Final

ALLOWED_PLACEHOLDERS: Final[frozenset[str]] = frozenset({"input", "context"})


class PromptTemplateError(ValueError):
    """Raised when a template contains an unsupported formatting expression."""


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    system: str
    user: str
    system_sha256: str
    user_sha256: str
    combined_sha256: str


@dataclass(frozen=True, slots=True)
class RenderedPromptSize:
    characters: int
    utf8_bytes: int


def validate_template(template: str) -> tuple[str, ...]:
    """Validate a format template and return unique placeholders in source order."""

    fields: list[str] = []
    seen: set[str] = set()
    placeholder_count = 0
    try:
        parsed = Formatter().parse(template)
        for _literal, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            placeholder_count += 1
            if placeholder_count > 20:
                raise PromptTemplateError("Prompt templates support at most 20 placeholders")
            if not field_name:
                raise PromptTemplateError("Anonymous placeholders are not allowed")
            if field_name not in ALLOWED_PLACEHOLDERS:
                raise PromptTemplateError(f"Unsupported prompt placeholder: {field_name}")
            if conversion is not None:
                raise PromptTemplateError("Prompt conversions are not allowed")
            if format_spec:
                raise PromptTemplateError("Prompt format specifications are not allowed")
            if field_name not in seen:
                seen.add(field_name)
                fields.append(field_name)
    except ValueError as exc:
        if isinstance(exc, PromptTemplateError):
            raise
        raise PromptTemplateError("Malformed prompt template") from exc
    return tuple(fields)


def estimate_rendered_prompt_size(
    *,
    system_template: str,
    user_template: str,
    input_text: str,
    context: str | None,
    expected_output: str | None,
) -> RenderedPromptSize:
    """Measure expansion without materializing attacker-controlled repeated values."""

    values = {
        "input": input_text,
        "context": context or "",
        "expected_output": expected_output or "",
    }

    def template_size(template: str) -> tuple[int, int]:
        characters = 0
        utf8_bytes = 0
        validate_template(template)
        for literal, field_name, _format_spec, _conversion in Formatter().parse(template):
            characters += len(literal)
            utf8_bytes += len(literal.encode("utf-8"))
            if field_name is not None:
                value = values[field_name]
                characters += len(value)
                utf8_bytes += len(value.encode("utf-8"))
        return characters, utf8_bytes

    system_characters, system_bytes = template_size(system_template)
    user_characters, user_bytes = template_size(user_template)
    return RenderedPromptSize(
        characters=system_characters + user_characters,
        utf8_bytes=system_bytes + user_bytes,
    )


def render_prompt(
    *,
    system_template: str,
    user_template: str,
    input_text: str,
    context: str | Sequence[str] | None = None,
    expected_output: str | None = None,
) -> RenderedPrompt:
    """Validate and render both prompts, then hash their exact UTF-8 bytes."""

    validate_template(system_template)
    validate_template(user_template)
    if context is None:
        context_text = ""
    elif isinstance(context, str):
        context_text = context
    else:
        context_text = "\n\n".join(context)
    values = {
        "input": input_text,
        "context": context_text,
        "expected_output": expected_output or "",
    }
    system = system_template.format_map(values)
    user = user_template.format_map(values)
    system_bytes = system.encode("utf-8")
    user_bytes = user.encode("utf-8")
    combined = (
        len(system_bytes).to_bytes(8, byteorder="big")
        + system_bytes
        + len(user_bytes).to_bytes(8, byteorder="big")
        + user_bytes
    )
    return RenderedPrompt(
        system=system,
        user=user,
        system_sha256=hashlib.sha256(system_bytes).hexdigest(),
        user_sha256=hashlib.sha256(user_bytes).hexdigest(),
        combined_sha256=hashlib.sha256(combined).hexdigest(),
    )
