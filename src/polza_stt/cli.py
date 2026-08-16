"""Точка входа CLI."""

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from . import __version__
from .api import run_all
from .audio import FFmpegMissing, probe_duration, require_ffmpeg, split_audio
from .config import (
    ask_config,
    default_config_path,
    load_config,
    migrate_local_env,
    remember_model,
)
from .console import console, fmt_duration, rub
from .dashboard import Dashboard
from .filepicker import pick_file
from .menu import confirm
from .models import estimate_cost, fetch_models, price_of


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="polza-stt",
        description="Параллельная транскрибация аудио через Polza.ai",
    )
    ap.add_argument("audio", nargs="?", help="путь к аудиофайлу (без него — выбор в UI)")
    ap.add_argument("-o", "--output", help="куда писать текст (по умолчанию <audio>.txt)")
    ap.add_argument("--chunk", type=int, default=300,
                    help="длина куска в секундах (по умолчанию 300)")
    ap.add_argument("--jobs", type=int, default=0,
                    help="ограничение одновременных запросов (0 = все куски сразу)")
    ap.add_argument("--language", default="ru", help="ISO-639-1 или auto (по умолчанию ru)")
    ap.add_argument("--model", help="ID модели (без него — выбор в UI)")
    ap.add_argument("--config", "--env", dest="config",
                    help="путь к конфигу (по умолчанию единый пользовательский)")
    ap.add_argument("--config-path", action="store_true",
                    help="показать путь к конфигу и выйти")
    ap.add_argument("--dir", default=".", help="каталог для выбора файла (по умолчанию текущий)")
    ap.add_argument("--reconfigure", action="store_true", help="перезаписать конфиг заново")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="не спрашивать подтверждение и модель")
    ap.add_argument("-V", "--version", action="version", version=f"polza-stt {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    if args.config_path:
        print(config_path)
        return 0

    try:
        require_ffmpeg()
    except FFmpegMissing as e:
        console.print(f"[red]{e}[/red]")
        return 1

    if not args.config:
        migrate_local_env(config_path)
    env = ask_config(config_path, {}) if args.reconfigure else load_config(config_path)

    interactive = console.is_terminal or sys.stdin.isatty()

    # 1. файл
    if args.audio:
        src = Path(os.path.expanduser(args.audio))
        if not src.is_file():
            console.print(f"[red]Файл не найден:[/red] {src}")
            return 1
    elif interactive:
        src = pick_file(Path(args.dir).expanduser())
    else:
        console.print("[red]Не указан аудиофайл[/red]")
        return 1

    duration = probe_duration(src)

    # 2. модель и цена
    models = fetch_models(env)
    if args.model:
        env["model"] = args.model
    elif interactive and not args.yes:
        env["model"] = pick_model_or_keep(models, env, duration)
    price = price_of(models, env["model"])
    estimate = estimate_cost(price, duration)

    out_path = Path(args.output) if args.output else src.with_suffix(".txt")

    info = Table.grid(padding=(0, 2))
    info.add_column(style="dim")
    info.add_column()
    info.add_row("файл", str(src))
    info.add_row("длительность", fmt_duration(duration) if duration else "неизвестна")
    info.add_row("модель",
                 env["model"] + (f"  ({price:.3f} ₽/мин)" if price is not None else ""))
    info.add_row("примерно стоимость", f"[bold magenta]{rub(estimate)}[/bold magenta]")
    info.add_row("кусок", f"{args.chunk} c")
    info.add_row("параллельно",
                 "все сразу (async)" if args.jobs <= 0 else f"не более {args.jobs} (async)")
    info.add_row("результат", str(out_path))
    console.print(Panel(info, title="Транскрибация", border_style="green"))

    if interactive and not args.yes and not confirm("Запускаем?"):
        console.print("[yellow]Отменено[/yellow]")
        return 0

    remember_model(config_path, env, env["model"])

    workdir = tempfile.mkdtemp(prefix="polza-stt_")
    texts: list[str] = []
    failed: list[str] = []
    costs: list[float | None] = []
    billed: list[float] = []
    try:
        with console.status("[bold]Нарезаю аудио через ffmpeg…", spinner="dots"):
            chunks, dropped = split_audio(src, workdir, args.chunk)
        if not chunks:
            console.print("[red]ffmpeg не создал ни одного куска[/red]")
            return 1
        tail = f" [dim](пустых хвостов выброшено: {dropped})[/dim]" if dropped else ""
        console.print(f"[green]✓[/green] Нарезано кусков: [bold]{len(chunks)}[/bold]{tail}")
        billed = [probe_duration(c) or 0.0 for c in chunks]

        with Dashboard([c.name for c in chunks]) as dash:
            texts, failed, costs = asyncio.run(
                run_all(chunks, env, args.language, args.jobs, dash)
            )

        out_path.write_text("\n\n".join(t for t in texts if t) + "\n", encoding="utf-8")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # факт: сумма из usage, если API её отдал; иначе тариф × реально отправленная длительность
    done_seconds = sum(sec for sec, t in zip(billed, texts) if t)
    if any(c is not None for c in costs):
        actual = sum(c for c in costs if c is not None)
        actual_label = "потрачено (по данным API)"
    elif price is not None:
        actual = price * done_seconds / 60
        actual_label = "потрачено (тариф × длительность)"
    else:
        actual, actual_label = None, "потрачено"

    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="dim")
    summary.add_column()
    summary.add_row("файл", str(out_path))
    summary.add_row("слов", str(sum(len(t.split()) for t in texts)))
    summary.add_row("обработано", fmt_duration(done_seconds) if done_seconds else "—")
    summary.add_row("оценка была", rub(estimate))
    summary.add_row(actual_label, f"[bold magenta]{rub(actual)}[/bold magenta]")

    if failed:
        summary.add_row("не удалось", f"[red]{', '.join(failed)}[/red]")
        console.print(Panel(summary, title="Завершено с ошибками", border_style="red"))
        return 2
    console.print(Panel(summary, title="Готово · временные файлы удалены", border_style="green"))
    return 0


def pick_model_or_keep(models: list[dict], env: dict, duration: float | None) -> str:
    from .models import pick_model

    return pick_model(models, current=env["model"], duration=duration) or env["model"]


def run() -> None:
    """Обёртка для console_scripts: KeyboardInterrupt не должен печатать трейс."""
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("[yellow]Прервано[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    run()
