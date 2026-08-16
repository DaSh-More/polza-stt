#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx>=0.27",
#     "platformdirs>=4.0",
#     "readchar>=4.0",
#     "rich>=13.7",
# ]
# ///
"""Запуск из репозитория без установки пакета: ./transcribe.py [аргументы]."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from polza_stt.cli import run  # noqa: E402

run()
