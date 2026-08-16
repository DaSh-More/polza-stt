"""Выбор аудиофайла: список в каталоге, системный диалог, ручной ввод."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Group
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from .audio import probe_duration
from .console import AUDIO_EXT, console, fmt_duration
from .menu import HINT, menu

# запускается отдельным процессом: Tk не должен жить рядом с TUI
TK_DIALOG = r"""
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = filedialog.askopenfilename(
    title="Выберите аудиофайл",
    initialdir=sys.argv[1],
    filetypes=[("Аудио", sys.argv[2]), ("Все файлы", "*")],
)
root.destroy()
sys.stdout.write(path or "")
"""


def gui_pick_file(initialdir: Path) -> tuple[bool, Path | None]:
    """Системный диалог выбора файла.

    Возвращает (диалог доступен, путь). tkinter — везде, zenity/kdialog — запасной
    вариант для Linux без tk. Путь None при отмене пользователем.
    """
    headless = (
        os.name != "nt"
        and sys.platform != "darwin"
        and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    )
    if headless:
        return False, None

    patterns = " ".join("*" + e for e in sorted(AUDIO_EXT))
    attempts = [[sys.executable, "-c", TK_DIALOG, str(initialdir), patterns]]
    if os.name != "nt":
        attempts.append(["zenity", "--file-selection", "--title=Выберите аудиофайл",
                         f"--filename={initialdir}/"])
        attempts.append(["kdialog", "--getopenfilename", str(initialdir)])

    for cmd in attempts:
        if cmd[0] != sys.executable and not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except Exception:
            continue
        picked = r.stdout.strip()
        if picked:
            return True, Path(picked)
        if r.returncode in (0, 1):  # диалог отработал, файл не выбран
            return True, None
    return False, None


def pick_file(directory: Path) -> Path:
    """Список аудиофайлов в каталоге + системный диалог + ручной ввод пути."""
    try:
        files = sorted(
            (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXT),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        files = []

    meta: list[tuple[Path, float | None]] = []
    if files:
        with console.status("[bold]Читаю длительности…", spinner="dots"):
            meta = [(p, probe_duration(p)) for p in files]
    else:
        console.print(f"[yellow]В {directory} аудиофайлов не нашлось[/yellow]")

    extras = ["открыть системный диалог…", "ввести путь вручную…"]

    def render(sel: int):
        table = Table(
            title=f"Аудио в {directory}", title_style="bold", header_style="bold blue"
        )
        table.add_column(" ", justify="center", style="dim")
        table.add_column("Файл", style="cyan")
        table.add_column("Размер", justify="right")
        table.add_column("Длительность", justify="right", style="magenta")
        for i, (p, d) in enumerate(meta):
            table.add_row(
                "›" if i == sel else "",
                p.name,
                f"{p.stat().st_size / 1e6:.1f} МБ",
                fmt_duration(d) if d else "—",
                style="bold black on white" if i == sel else "",
            )
        for n, label in enumerate(extras):
            i = len(meta) + n
            table.add_row(
                "›" if i == sel else "", label, "", "",
                style="bold black on white" if i == sel else "dim",
            )
        return Group(table, Text.from_markup(HINT))

    while True:
        sel = menu(render, len(meta) + len(extras), 0)
        if sel < len(meta):
            chosen = meta[sel][0]
            console.print(f"[green]✓[/green] Файл: [cyan]{chosen.name}[/cyan]")
            return chosen

        manual = sel == len(meta) + 1
        if not manual:
            with console.status("[bold]Открыл системный диалог…", spinner="dots"):
                available, picked = gui_pick_file(directory)
            if picked and picked.is_file():
                console.print(f"[green]✓[/green] Файл: [cyan]{picked}[/cyan]")
                return picked
            if available:
                console.print("[yellow]Файл не выбран[/yellow]")
                continue  # обратно в список
            console.print("[yellow]![/yellow] Системный диалог недоступен, введите путь")

        raw = Prompt.ask("Путь к аудиофайлу", console=console).strip().strip("'\"")
        p = Path(os.path.expanduser(raw))
        if p.is_file():
            console.print(f"[green]✓[/green] Файл: [cyan]{p}[/cyan]")
            return p
        console.print(f"[red]Нет такого файла:[/red] {p}")
