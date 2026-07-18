"""Wait for one local HTTP endpoint without printing response bodies."""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: wait_for_http.py URL")
    url = sys.argv[1]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - CI-local URL.
                if 200 <= response.status < 300:
                    return 0
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
