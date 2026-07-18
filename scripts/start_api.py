"""Apply migrations and start the single-worker container API."""

from __future__ import annotations

import uvicorn

from evalforge.config import get_settings


def main() -> None:
    """Start the API; its application lifespan owns optional schema migration."""
    settings = get_settings()
    uvicorn.run(
        "evalforge.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        workers=1,
        access_log=False,
    )


if __name__ == "__main__":
    main()
