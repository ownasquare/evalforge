"""Idempotent deterministic portfolio data."""

from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalforge.models import ApiMode, Dataset, ModelProfile, PromptTemplate
from evalforge.repositories import Repositories
from evalforge.schemas import (
    DatasetCreate,
    ModelProfileCreate,
    PromptTemplateCreate,
    TestCaseCreate,
)

DEMO_DATASET_FILES = ("customer-support.json", "rag-groundedness.json")


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized[:180] or "case"


def _read_dataset(filename: str, *, example_root: Path | None) -> DatasetCreate:
    if example_root is None:
        raw = files("evalforge").joinpath("data", filename).read_text(encoding="utf-8")
    else:
        raw = (example_root / filename).read_text(encoding="utf-8")
    payload = json.loads(raw)
    cases: list[TestCaseCreate] = []
    for position, raw in enumerate(payload["cases"]):
        context_value = raw.get("context")
        if isinstance(context_value, list):
            context_chunks = [str(item) for item in context_value]
            context_text = "\n\n".join(context_chunks)
        elif context_value is None:
            context_chunks = []
            context_text = None
        else:
            context_chunks = [str(context_value)]
            context_text = str(context_value)
        metadata = dict(raw.get("metadata") or {})
        metadata["display_name"] = raw["name"]
        metadata["relevance_keywords"] = list(raw.get("relevance_keywords") or [])
        cases.append(
            TestCaseCreate(
                external_id=_slug(str(raw["name"])),
                position=position,
                input_text=str(raw["input"]),
                context_text=context_text,
                context_chunks=context_chunks,
                expected_output=raw.get("expected_output"),
                required_phrases=list(raw.get("required_phrases") or []),
                constraints_json=dict(raw.get("criteria") or {}),
                tags=list(raw.get("tags") or []),
                metadata_json=metadata,
            )
        )
    return DatasetCreate(
        name=str(payload["name"]),
        description=payload.get("description"),
        version=1,
        metadata_json={"source": "evalforge_demo", "fixture_backed": True},
        cases=cases,
    )


def seed_demo(session: Session, *, example_root: Path | None = None) -> dict[str, int]:
    """Create stable demo resources once and return total demo resource counts."""
    repositories = Repositories(session)

    existing_dataset_names = set(session.scalars(select(Dataset.name)))
    for filename in DEMO_DATASET_FILES:
        dataset_data = _read_dataset(filename, example_root=example_root)
        if dataset_data.name not in existing_dataset_names:
            repositories.datasets.create(dataset_data)
            existing_dataset_names.add(dataset_data.name)

    prompt_specs = (
        PromptTemplateCreate(
            name="Grounded answer",
            description="Answer only from supplied evidence and state when evidence is missing.",
            version=1,
            system_template=(
                "You are a careful evaluation assistant. Use only the supplied evidence and do "
                "not invent facts."
            ),
            user_template="Question:\n{input}\n\nEvidence:\n{context}\n\nGive a direct answer.",
            metadata_json={"source": "evalforge_demo"},
        ),
        PromptTemplateCreate(
            name="Concise grounded answer",
            description="A shorter grounded prompt variant for paired comparison.",
            version=1,
            system_template="Answer from evidence only. Be concise and explicit about uncertainty.",
            user_template="{input}\n\nEvidence:\n{context}\n\nAnswer in at most two sentences.",
            metadata_json={"source": "evalforge_demo"},
        ),
    )
    existing_prompt_keys = {
        (name, version)
        for name, version in session.execute(select(PromptTemplate.name, PromptTemplate.version))
    }
    for prompt_data in prompt_specs:
        if (prompt_data.name, prompt_data.version) not in existing_prompt_keys:
            repositories.prompts.create(prompt_data)
            existing_prompt_keys.add((prompt_data.name, prompt_data.version))

    model_specs = (
        ModelProfileCreate(
            name="Demo Reliable",
            description="Reference-aligned deterministic output for the offline happy path.",
            version=1,
            provider="demo",
            model_name="demo-reliable",
            api_mode=ApiMode.DETERMINISTIC,
            generation_parameters={"temperature": 0.0, "max_output_tokens": 512, "seed": 7},
            enabled=True,
            metadata_json={
                "source": "evalforge_demo",
                "synthetic": True,
                "profile": "balanced",
                "pricing_known": True,
            },
        ),
        ModelProfileCreate(
            name="Demo Fast",
            description="Concise deterministic output with lower synthetic latency.",
            version=1,
            provider="demo",
            model_name="demo-fast",
            api_mode=ApiMode.DETERMINISTIC,
            generation_parameters={"temperature": 0.0, "max_output_tokens": 128, "seed": 7},
            enabled=True,
            metadata_json={
                "source": "evalforge_demo",
                "synthetic": True,
                "profile": "concise",
                "pricing_known": True,
            },
        ),
        ModelProfileCreate(
            name="Demo Risky",
            description="Injects unsupported numbers and URLs to demonstrate risk detection.",
            version=1,
            provider="demo",
            model_name="demo-risky",
            api_mode=ApiMode.DETERMINISTIC,
            generation_parameters={"temperature": 0.0, "max_output_tokens": 512, "seed": 7},
            enabled=True,
            metadata_json={
                "source": "evalforge_demo",
                "synthetic": True,
                "profile": "hallucinating",
                "pricing_known": True,
            },
        ),
    )
    existing_model_keys = {
        (name, version)
        for name, version in session.execute(select(ModelProfile.name, ModelProfile.version))
    }
    for model_data in model_specs:
        if (model_data.name, model_data.version) not in existing_model_keys:
            repositories.models.create(model_data)
            existing_model_keys.add((model_data.name, model_data.version))

    session.flush()
    return demo_counts(session)


def demo_counts(session: Session) -> dict[str, int]:
    """Return safe aggregate counts for idempotency checks and CLI output."""
    dataset_names = {"Customer support quality", "Grounded product Q&A"}
    prompt_names = {"Grounded answer", "Concise grounded answer"}
    model_names = {"Demo Reliable", "Demo Fast", "Demo Risky"}
    return {
        "datasets": len(
            list(session.scalars(select(Dataset.id).where(Dataset.name.in_(dataset_names))))
        ),
        "prompts": len(
            list(
                session.scalars(
                    select(PromptTemplate.id).where(PromptTemplate.name.in_(prompt_names))
                )
            )
        ),
        "models": len(
            list(session.scalars(select(ModelProfile.id).where(ModelProfile.name.in_(model_names))))
        ),
    }


def exportable_seed_manifest(session: Session) -> dict[str, Any]:
    """Return IDs and names without prompt or case content."""
    return {
        "datasets": [
            {"id": item.id, "name": item.name, "version": item.version}
            for item in session.scalars(
                select(Dataset).where(
                    Dataset.name.in_({"Customer support quality", "Grounded product Q&A"})
                )
            )
        ],
        "prompts": [
            {"id": item.id, "name": item.name, "version": item.version}
            for item in session.scalars(
                select(PromptTemplate).where(
                    PromptTemplate.name.in_({"Grounded answer", "Concise grounded answer"})
                )
            )
        ],
        "models": [
            {"id": item.id, "name": item.name, "version": item.version}
            for item in session.scalars(
                select(ModelProfile).where(
                    ModelProfile.name.in_({"Demo Reliable", "Demo Fast", "Demo Risky"})
                )
            )
        ],
    }
