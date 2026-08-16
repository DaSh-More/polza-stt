"""Работа с ffmpeg/ffprobe."""

import re
import shutil
import subprocess
from pathlib import Path


class FFmpegMissing(RuntimeError):
    """ffmpeg или ffprobe не найдены в PATH."""


def require_ffmpeg() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        raise FFmpegMissing(
            f"не найдены {', '.join(missing)} — поставьте ffmpeg "
            "(apt install ffmpeg / brew install ffmpeg / winget install Gyan.FFmpeg)"
        )


def probe_duration(src: Path) -> float | None:
    """Длительность в секундах или None, если ffprobe не смог."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(src)],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


# короче этого куски не отправляем: обычно это хвост в доли секунды,
# который ffmpeg отрезает, когда длительность чуть больше кратной segment_time
MIN_CHUNK_SECONDS = 1.0


def split_audio(src: Path, workdir: str | Path, seconds: int) -> tuple[list[Path], int]:
    """Режет аудио на куски по seconds, перекодируя в mono mp3 64k (запас по лимиту 15МБ).

    Возвращает (куски, сколько пустых хвостов выброшено).
    """
    pattern = str(Path(workdir) / "chunk_%03d.mp3")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
         "-f", "segment", "-segment_time", str(seconds), "-reset_timestamps", "1",
         "-segment_start_number", "1", pattern],
        check=True,
    )

    # сортировка по числу, а не по строке: после 999 ffmpeg печатает четыре цифры
    def number(p: Path) -> int:
        m = re.search(r"(\d+)", p.stem)
        return int(m.group(1)) if m else 0

    chunks, dropped = [], 0
    for chunk in sorted(Path(workdir).glob("chunk_*.mp3"), key=number):
        d = probe_duration(chunk)
        if d is None or d < MIN_CHUNK_SECONDS:
            chunk.unlink(missing_ok=True)
            dropped += 1
            continue
        chunks.append(chunk)
    return chunks, dropped
