from __future__ import annotations

import sys

from zug2ferd.app import run


def main() -> int:
    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
