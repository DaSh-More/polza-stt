"""Тесты чистой логики: без сети и без ffmpeg."""

import httpx
import pytest

from polza_stt.api import _parse_result, _poll_job, transcribe_chunk
from polza_stt.config import (
    STORED,
    ask_config,
    load_config,
    read_env_file,
    remember_model,
    write_env_file,
)
from polza_stt.console import fmt_duration, rub
from polza_stt.models import estimate_cost, price_of

MODELS = [
    {"id": "a/one", "name": "One", "price": 0.1},
    {"id": "b/two", "name": "Two", "price": None},
]


def test_fmt_duration():
    assert fmt_duration(0) == "0:00"
    assert fmt_duration(59) == "0:59"
    assert fmt_duration(300) == "5:00"
    assert fmt_duration(4398) == "1:13:18"


def test_rub():
    assert rub(None) == "—"
    assert rub(0.6) == "0.60 ₽"


def test_price_and_estimate():
    assert price_of(MODELS, "a/one") == 0.1
    assert price_of(MODELS, "b/two") is None
    assert price_of(MODELS, "нет такой") is None
    assert estimate_cost(0.12, 300) == pytest.approx(0.6)
    assert estimate_cost(None, 300) is None
    assert estimate_cost(0.12, None) is None


def test_parse_result_cost_variants():
    assert _parse_result({"text": " привет "}) == ("привет", None)
    assert _parse_result({"text": "x", "usage": {"cost_rub": "1.5"}}) == ("x", 1.5)
    assert _parse_result({"text": "x", "usage": {"cost": 2}}) == ("x", 2.0)


def test_config_roundtrip(tmp_path, monkeypatch):
    for key in STORED:
        monkeypatch.delenv(key.upper(), raising=False)
        monkeypatch.delenv("POLZA_" + key.upper(), raising=False)
    cfg = tmp_path / "config.env"
    write_env_file(cfg, {"base_url": "https://x/api/v1", "token": "t", "model": "m/1"})
    assert read_env_file(cfg) == {"base_url": "https://x/api/v1", "token": "t", "model": "m/1"}
    assert cfg.stat().st_mode & 0o777 == 0o600


def test_config_skips_empty_model(tmp_path, monkeypatch):
    for key in STORED:
        monkeypatch.delenv(key.upper(), raising=False)
        monkeypatch.delenv("POLZA_" + key.upper(), raising=False)
    cfg = tmp_path / "config.env"
    write_env_file(cfg, {"base_url": "u", "token": "t"})
    assert "MODEL" not in cfg.read_text()
    assert set(read_env_file(cfg)) == {"base_url", "token"}


def test_load_config_fills_defaults(tmp_path, monkeypatch):
    for key in STORED:
        monkeypatch.delenv(key.upper(), raising=False)
        monkeypatch.delenv("POLZA_" + key.upper(), raising=False)
    cfg = tmp_path / "config.env"
    cfg.write_text("TOKEN=t\n", encoding="utf-8")
    env = load_config(cfg)
    assert env["token"] == "t"
    assert env["base_url"] and env["model"]  # подставлены сами, ничего не спрашивая


def test_ask_config_only_asks_token(tmp_path, monkeypatch):
    for key in STORED:
        monkeypatch.delenv(key.upper(), raising=False)
        monkeypatch.delenv("POLZA_" + key.upper(), raising=False)
    asked = []

    def fake_ask(prompt, **kw):
        asked.append(prompt)
        return "secret"

    monkeypatch.setattr("polza_stt.config.Prompt.ask", fake_ask)
    cfg = tmp_path / "config.env"
    env = ask_config(cfg, {})
    assert [p.strip() for p in asked] == ["API token"]
    assert env["token"] == "secret"
    assert read_env_file(cfg)["base_url"]


def test_remember_model(tmp_path, monkeypatch):
    for key in STORED:
        monkeypatch.delenv(key.upper(), raising=False)
        monkeypatch.delenv("POLZA_" + key.upper(), raising=False)
    cfg = tmp_path / "config.env"
    env = {"base_url": "u", "token": "t"}
    write_env_file(cfg, env)
    remember_model(cfg, env, "x/first")
    assert read_env_file(cfg)["model"] == "x/first"
    remember_model(cfg, env, "y/second")
    assert read_env_file(cfg)["model"] == "y/second"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_sync_model(tmp_path):
    chunk = tmp_path / "chunk_001.mp3"
    chunk.write_bytes(b"audio")

    def handler(request):
        return httpx.Response(200, json={"text": "привет", "usage": {"cost_rub": 0.5}})

    async with _client(handler) as c:
        assert await transcribe_chunk(
            c, chunk, {"base_url": "https://x/v1", "token": "t", "model": "m"}, "ru"
        ) == ("привет", 0.5)


@pytest.mark.asyncio
async def test_async_model_is_polled(tmp_path, monkeypatch):
    monkeypatch.setattr("polza_stt.api.POLL_INTERVAL", 0)
    chunk = tmp_path / "chunk_001.mp3"
    chunk.write_bytes(b"audio")
    calls = {"n": 0}

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "gen_1", "status": "processing"})
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(200, json={"id": "gen_1", "status": "processing"})
        return httpx.Response(200, json={"id": "gen_1", "status": "completed", "text": "готово"})

    async with _client(handler) as c:
        text, cost = await transcribe_chunk(
            c, chunk, {"base_url": "https://x/v1", "token": "t", "model": "m"}, "ru"
        )
    assert (text, cost) == ("готово", None)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_failed_job_raises(monkeypatch):
    monkeypatch.setattr("polza_stt.api.POLL_INTERVAL", 0)

    def handler(request):
        return httpx.Response(200, json={"id": "gen_1", "status": "failed"})

    async with _client(handler) as c:
        with pytest.raises(RuntimeError, match="failed"):
            await _poll_job(c, {"base_url": "https://x/v1", "token": "t"}, "gen_1")


@pytest.mark.asyncio
async def test_empty_text_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr("polza_stt.api.asyncio.sleep", lambda *_: _noop())
    chunk = tmp_path / "chunk_001.mp3"
    chunk.write_bytes(b"audio")

    def handler(request):
        return httpx.Response(200, json={"text": ""})

    async with _client(handler) as c:
        with pytest.raises(RuntimeError, match="пустой ответ"):
            await transcribe_chunk(
                c, chunk, {"base_url": "https://x/v1", "token": "t", "model": "m"}, "ru"
            )


async def _noop():
    return None


class _Dash:
    """Заглушка дашборда: молча принимает статусы."""

    def set(self, *a, **kw):
        pass


@pytest.mark.asyncio
async def test_auto_concurrency_cap(tmp_path, monkeypatch):
    """jobs=0 не должен пускать в полёт больше AUTO_JOBS запросов сразу."""
    import polza_stt.api as api

    monkeypatch.setattr(api, "AUTO_JOBS", 4)
    chunks = []
    for i in range(1, 13):
        p = tmp_path / f"chunk_{i:03d}.mp3"
        p.write_bytes(b"audio")
        chunks.append(p)

    state = {"now": 0, "max": 0}

    async def handler(request):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return httpx.Response(200, json={"text": "ок"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient  # ссылка до подмены, иначе рекурсия
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda **kw: real_client(transport=transport))
    texts, failed, _ = await api.run_all(
        chunks, {"base_url": "https://x/v1", "token": "t", "model": "m"}, "ru", 0, _Dash()
    )
    assert not failed and texts == ["ок"] * 12
    assert state["max"] <= 4, f"в полёте было {state['max']} запросов при потолке 4"


@pytest.mark.asyncio
async def test_retry_sends_audio_not_error_text(tmp_path, monkeypatch):
    """Повтор должен слать то же тело запроса, а не текст прошлой ошибки."""
    monkeypatch.setattr("polza_stt.api.asyncio.sleep", lambda *_: _noop())
    chunk = tmp_path / "chunk_001.mp3"
    chunk.write_bytes(b"audio")
    bodies = []

    def handler(request):
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(500, text="боль на сервере")
        return httpx.Response(200, json={"text": "ок"})

    async with _client(handler) as c:
        text, _ = await transcribe_chunk(
            c, chunk, {"base_url": "https://x/v1", "token": "t", "model": "m"}, "ru"
        )
    assert text == "ок"
    assert bodies[0] == bodies[1], "второй запрос ушёл с другим телом"
    assert b"base64" in bodies[1]


import asyncio  # noqa: E402  (нужен тестам выше)


@pytest.mark.asyncio
async def test_auto_language_omits_field(tmp_path):
    """language=auto — поле не отправляем, пусть API определяет сам."""
    chunk = tmp_path / "chunk_001.mp3"
    chunk.write_bytes(b"audio")
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"text": "ок"})

    env = {"base_url": "https://x/v1", "token": "t", "model": "m"}
    async with _client(handler) as c:
        await transcribe_chunk(c, chunk, env, "auto")
    assert "language" not in seen
    async with _client(handler) as c:
        await transcribe_chunk(c, chunk, env, "ru")
    assert seen["language"] == "ru"


import json  # noqa: E402
