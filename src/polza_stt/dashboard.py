"""Живой экран прогресса транскрибации."""

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from .console import console


class Dashboard:
    """Список кусков со статусами + общий прогресс."""

    STYLES = {
        "wait": ("[dim]· ожидает[/dim]", "dim"),
        "work": ("[yellow]● в работе[/yellow]", ""),
        "done": ("[green]✓ готово[/green]", ""),
        "fail": ("[red]✗ ошибка[/red]", ""),
    }

    def __init__(self, names: list[str]):
        self.state = {n: ["wait", ""] for n in names}
        self.names = names
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        )
        self.task = self.progress.add_task("Транскрибация", total=len(names))
        self.live = Live(self.render(), console=console, refresh_per_second=8)

    def render(self):
        table = Table.grid(padding=(0, 2))
        table.add_column(style="cyan", no_wrap=True)
        table.add_column(no_wrap=True)
        table.add_column(style="dim", overflow="ellipsis", max_width=60)
        for name in self.names:
            status, note = self.state[name]
            label, style = self.STYLES[status]
            table.add_row(Text(name, style=style), label, note)
        return Group(
            Panel(table, title="Куски", border_style="blue", padding=(0, 1)),
            self.progress,
        )

    def set(self, name: str, status: str, note: str = "", advance: bool = False):
        self.state[name] = [status, note]
        if advance:
            self.progress.advance(self.task)
        self.live.update(self.render())

    def __enter__(self):
        self.live.__enter__()
        return self

    def __exit__(self, *exc):
        self.live.__exit__(*exc)
