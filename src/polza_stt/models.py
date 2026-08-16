"""Список STT-моделей, цены и выбор модели."""

import httpx
from rich.console import Group
from rich.table import Table
from rich.text import Text

from .console import DEFAULT_BASE_URL, console, rub
from .menu import HINT, menu

# запасной список, если /models недоступен (цены — руб./мин)
FALLBACK_MODELS = [
    {"id": "openai/whisper-large-v3-turbo", "name": "Whisper Large V3 Turbo", "price": 0.048},
    {"id": "openai/whisper-large-v3", "name": "Whisper Large V3", "price": 0.108},
    {"id": "ai-sage/gigaam-v3", "name": "GigaAM-v3", "price": 0.252},
    {"id": "openai/whisper-1", "name": "Whisper 1", "price": 0.432},
]


def fetch_models(env: dict) -> list[dict]:
    """Список STT-моделей с ценой руб./мин из /models."""
    base = env.get("base_url", DEFAULT_BASE_URL).rstrip("/")
    try:
        with console.status("[bold]Загружаю список моделей…", spinner="dots"):
            r = httpx.get(
                base + "/models",
                headers={"Authorization": f"Bearer {env['token']}"},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json().get("data", [])
    except Exception as e:
        console.print(
            f"[yellow]![/yellow] Не удалось получить список моделей ({e}), беру встроенный"
        )
        return list(FALLBACK_MODELS)

    models = []
    for m in data:
        if m.get("type") != "stt":
            continue
        pricing = (m.get("top_provider") or {}).get("pricing") or {}
        try:
            price = float(pricing.get("stt_per_minute"))
        except (TypeError, ValueError):
            price = None
        models.append({"id": m["id"], "name": m.get("name") or m["id"], "price": price})
    models.sort(key=lambda m: (m["price"] is None, m["price"] or 0))
    return models or list(FALLBACK_MODELS)


def price_of(models: list[dict], model_id: str) -> float | None:
    for m in models:
        if m["id"] == model_id:
            return m.get("price")
    return None


def estimate_cost(price: float | None, duration: float | None) -> float | None:
    return price * duration / 60 if (price is not None and duration) else None


def pick_model(
    models: list[dict], current: str | None, duration: float | None = None
) -> str | None:
    """Таблица моделей с ценами и примерной стоимостью файла; возвращает выбранный id."""
    if not models:
        return current
    start = next((i for i, m in enumerate(models) if m["id"] == current), 0)

    def render(sel: int):
        table = Table(
            title="Модели транскрибации", title_style="bold", header_style="bold blue"
        )
        table.add_column(" ", justify="center", style="dim")
        table.add_column("Модель")
        table.add_column("ID", style="cyan")
        table.add_column("Руб./мин", justify="right")
        if duration:
            table.add_column("За этот файл", justify="right", style="magenta")
        for i, m in enumerate(models):
            mark = " [green]•[/green]" if m["id"] == current else ""
            row = [
                "›" if i == sel else "",
                m["name"] + mark,
                m["id"],
                f"{m['price']:.3f}" if m.get("price") is not None else "—",
            ]
            if duration:
                row.append(rub(estimate_cost(m.get("price"), duration)))
            table.add_row(*row, style="bold black on white" if i == sel else "")
        foot = HINT + ("  [dim]· • — модель из конфига[/dim]" if current else "")
        return Group(table, Text.from_markup(foot))

    chosen = models[menu(render, len(models), start)]
    console.print(f"[green]✓[/green] Модель: [cyan]{chosen['id']}[/cyan]")
    return chosen["id"]
