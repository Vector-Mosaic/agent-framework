"""Run only in an isolated test display, never an existing user's clipboard.

Example: python tests/credential_adapters/linux_clipboard_smoke.py x11
The surrounding qualifier owns Xvfb/compositor lifetime and isolated DISPLAY.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from repo_platform.credentials.clipboard import open_clipboard


def main():
    backend = sys.argv[1]
    first = open_clipboard(backend)
    second = open_clipboard(backend)
    try:
        first.set_text("synthetic-first")
        assert first.get_text() == "synthetic-first"
        second.set_text("synthetic-newer")
        assert second.get_text() == "synthetic-newer"
        first.cleanup("synthetic-first")
        assert second.get_text() == "synthetic-newer", "cleanup overwrote newer clipboard"
    finally:
        first.cleanup("synthetic-first")
        second.cleanup("synthetic-newer")
    print("PASS: real owned selection roundtrip and newer clipboard preservation")


if __name__ == "__main__":
    main()
