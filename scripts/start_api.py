"""Apply migrations and start the single-worker container API."""

from __future__ import annotations

import uvicorn

from evalforge.config import get_settings
from evalforge.container import apply_migrations


def main() -> None:
    """Upgrade the schema before starting the production-shaped local service."""
    apply_migrations(get_settings())
    uvicorn.run(
        "evalforge.api.app:app",
        host="0.0.0.0",  # noqa: S104 - container ingress is loopback-published by Compose.
        port=8000,
        workers=1,
        access_log=False,
    )


if __name__ == "__main__":
    main()
