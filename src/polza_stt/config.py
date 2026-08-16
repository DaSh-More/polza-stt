"""Чтение и интерактивное заполнение конфига (.env)."""

import os
import sys
from pathlib import Path

from platformdirs import user_config_dir
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from .console import DEFAULT_BASE_URL, DEFAULT_MODEL, console

# без чего запуск невозможен; base_url и model подставляем сами, их можно
# поправить в конфиге вручную
REQUIRED = ("token",)
STORED = ("base_url", "token", "model")


def user_config_path() -> Path:
    """Единое место хранения конфига, одинаковое для любого рабочего каталога.

    Linux: ~/.config/polza-stt/config.env (или $XDG_CONFIG_HOME)
    macOS: ~/Library/Application Support/polza-stt/config.env
    Windows: %APPDATA%\\polza-stt\\config.env
    Переопределяется переменной POLZA_STT_CONFIG.
    """
    override = os.environ.get("POLZA_STT_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path(user_config_dir("polza-stt", appauthor=False)) / "config.env"


def default_config_path() -> Path:
    """Конфиг всегда один и тот же; локальный .env — только через --config."""
    return user_config_path()


def migrate_local_env(target: Path) -> bool:
    """Переносит ./.env в единое хранилище при первом запуске. True, если перенесли."""
    local = Path(".env")
    if target.exists() or not local.is_file():
        return False
    env = read_env_file(local)
    if any(not env.get(k) for k in REQUIRED):
        return False
    write_env_file(target, env)
    console.print(f"[green]✓[/green] Перенёс настройки из {local.resolve()} в [cyan]{target}[/cyan]")
    return True


def read_env_file(path: Path) -> dict:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip().lower()] = v.strip().strip("'\"")
    for key in STORED:
        if not env.get(key):
            from_os = os.environ.get(key.upper()) or os.environ.get("POLZA_" + key.upper())
            if from_os:
                env[key] = from_os
    return env


def write_env_file(path: Path, env: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(f"{k.upper()}={env.get(k, '')}" for k in STORED if env.get(k)) + "\n",
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def ask_config(path: Path, env: dict) -> dict:
    """Спрашивает недостающие поля и сохраняет конфиг. Модель здесь не спрашиваем —
    она запоминается сама после первого запуска."""
    missing = [k for k in REQUIRED if not env.get(k)]
    console.print(
        Panel(
            Text.from_markup(
                f"Нужен API-ключ Polza.ai.\n"
                f"Сохраню его в [cyan]{path}[/cyan] — данные останутся локально.\n"
                f"[dim]Адрес API ({DEFAULT_BASE_URL}) и модель подставлю сам, "
                f"их можно поправить в этом же файле.[/dim]"
            ),
            title="Настройка",
            border_style="yellow",
        )
    )
    if "token" in missing:
        token = ""
        while not token:
            token = Prompt.ask("  API token", password=True, console=console).strip()
        env["token"] = token
    env.setdefault("base_url", DEFAULT_BASE_URL)

    write_env_file(path, env)
    console.print(f"[green]✓[/green] Сохранил {path}\n")
    return env


def load_config(path: Path) -> dict:
    env = read_env_file(path)
    if any(not env.get(k) for k in REQUIRED):
        if not console.is_terminal and not sys.stdin.isatty():
            sys.exit(f"В {path} нужен TOKEN (терминал недоступен для ввода)")
        env = ask_config(path, env)
    env.setdefault("base_url", DEFAULT_BASE_URL)
    env.setdefault("model", DEFAULT_MODEL)
    return env


def remember_model(path: Path, env: dict, model: str) -> None:
    """Запоминает последнюю использованную модель в конфиге."""
    if not model or read_env_file(path).get("model") == model:
        return
    stored = read_env_file(path)
    stored.update({k: env[k] for k in ("base_url", "token") if env.get(k)})
    stored["model"] = model
    try:
        write_env_file(path, stored)
    except OSError as e:
        console.print(f"[yellow]![/yellow] Не смог записать модель в конфиг: {e}")
