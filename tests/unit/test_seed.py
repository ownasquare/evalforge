import json
from importlib.resources import files
from pathlib import Path

from sqlalchemy import select

from evalforge.database import Base, create_database_engine, create_session_factory, session_scope
from evalforge.models import TestCase as DomainTestCase
from evalforge.seed import DEMO_DATASET_FILES, seed_demo

ROOT = Path(__file__).parents[2]


def test_demo_seed_is_idempotent(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as session:
            first = seed_demo(session)
        with session_scope(factory) as session:
            second = seed_demo(session)
        with factory() as session:
            seeded_cases = list(session.scalars(select(DomainTestCase)))
        assert first == second == {"datasets": 2, "prompts": 2, "models": 3}
        assert any(len(case.context_chunks) > 1 for case in seeded_cases)
        assert all(
            case.context_text == "\n\n".join(case.context_chunks)
            for case in seeded_cases
            if case.context_chunks
        )
    finally:
        engine.dispose()


def test_packaged_demo_fixtures_match_the_public_examples() -> None:
    for filename in DEMO_DATASET_FILES:
        packaged = json.loads(
            files("evalforge").joinpath("data", filename).read_text(encoding="utf-8")
        )
        public = json.loads((ROOT / "examples" / filename).read_text(encoding="utf-8"))
        assert packaged == public
