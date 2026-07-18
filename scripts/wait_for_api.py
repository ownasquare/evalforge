"""Wait for API readiness without an external command-line dependency."""

from __future__ import annotations

import argparse
import json
import time
from urllib.error import URLError
from urllib.request import urlopen


def wait_for_api(url: str, timeout_seconds: float) -> bool:
    """Return whether the readiness endpoint became healthy within the deadline."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310 - operator supplies local URL.
                payload = json.loads(response.read())
                if response.status == 200 and payload.get("status") == "ready":
                    return True
        except (OSError, URLError, ValueError):
            pass
        time.sleep(0.25)
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/health/ready")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    raise SystemExit(0 if wait_for_api(args.url, args.timeout) else 1)


if __name__ == "__main__":
    main()
