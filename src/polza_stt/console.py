"""Общая консоль и мелкие форматтеры."""

from rich.console import Console

# всё оформление уходит в stderr, чтобы stdout можно было пайпить
console = Console(stderr=True)

DEFAULT_BASE_URL = "https://polza.ai/api/v2"
DEFAULT_MODEL = "ai-sage/gigaam-v3"

AUDIO_EXT = {
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
    ".oga",
    ".opus",
    ".webm",
    ".mp4",
    ".mka",
    ".aac",
    ".wma",
}


def rub(value: float | None) -> str:
    return f"{value:.2f} ₽" if value is not None else "—"


def fmt_duration(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
