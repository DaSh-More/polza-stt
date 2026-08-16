"""Меню, управляемое стрелками, поверх rich.Live."""

import sys

import readchar
from rich.console import Group
from rich.live import Live
from rich.text import Text

from .console import console

HINT = "[dim]↑/↓ — выбор · Enter — подтвердить · q — выход[/dim]"


def menu(render, count: int, start: int = 0) -> int:
    """Живое меню: render(idx) отдаёт renderable с подсвеченной строкой idx."""
    idx = max(0, min(start, count - 1))
    with Live(render(idx), console=console, auto_refresh=False, transient=True) as live:
        while True:
            key = readchar.readkey()
            if key in (readchar.key.UP, readchar.key.LEFT, "k"):
                idx = (idx - 1) % count
            elif key in (readchar.key.DOWN, readchar.key.RIGHT, "j"):
                idx = (idx + 1) % count
            elif key in (readchar.key.HOME, "g"):
                idx = 0
            elif key in (readchar.key.END, "G"):
                idx = count - 1
            elif key in (readchar.key.ENTER, "\r", "\n"):
                return idx
            elif key in (readchar.key.CTRL_C, readchar.key.ESC, "q"):
                console.print("[yellow]Отменено[/yellow]")
                sys.exit(0)
            elif key.isdigit() and key != "0" and int(key) <= count:
                idx = int(key) - 1
            live.update(render(idx), refresh=True)


def confirm(question: str, default_yes: bool = True) -> bool:
    """Да/нет стрелками."""

    def render(i: int):
        opts = []
        for n, label in enumerate(("Да, запускаем", "Нет, отмена")):
            style = "bold black on green" if n == i else "dim"
            opts.append(Text(f" {label} ", style=style))
        return Group(
            Text.from_markup(f"[bold]{question}[/bold]"),
            Text("  ").join(opts),
            Text.from_markup("[dim]←/→ — выбор · Enter — подтвердить[/dim]"),
        )

    return menu(render, 2, start=0 if default_yes else 1) == 0
