"""Запросы к Polza.ai: один кусок и параллельный прогон всех кусков."""

import asyncio
import base64
import json
from pathlib import Path

import httpx


AUTO_JOBS = 16  # потолок одновременных запросов, когда --jobs не задан
PENDING = {"processing", "queued", "pending", "in_progress", "running", "starting"}
POLL_INTERVAL = 3.0
POLL_TIMEOUT = 1800.0


def _parse_result(data: dict) -> tuple[str, float | None]:
    usage = data.get("usage") or {}
    cost = usage.get("cost_rub", usage.get("cost"))
    return (data.get("text") or "").strip(), (float(cost) if cost is not None else None)


async def _poll_job(
    client: httpx.AsyncClient, env: dict, job_id: str, on_wait=None
) -> tuple[str, float | None]:
    """Асинхронные модели отдают id задачи; ждём готовности по GET .../{id}."""
    url = env["base_url"].rstrip("/") + f"/audio/transcriptions/{job_id}"
    headers = {"Authorization": f"Bearer {env['token']}"}
    waited = 0.0
    while waited < POLL_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
        status = (data.get("status") or "").lower()
        if status in PENDING:
            if on_wait:
                on_wait(waited)
            continue
        if data.get("text") or status in ("completed", "succeeded", "success", "done"):
            return _parse_result(data)
        raise RuntimeError(f"задача {job_id}: статус {status or 'неизвестен'}")
    raise RuntimeError(f"задача {job_id}: не дождались результата за {POLL_TIMEOUT:.0f} c")


async def transcribe_chunk(
    client: httpx.AsyncClient,
    chunk: Path,
    env: dict,
    language: str,
    on_retry=None,
    on_wait=None,
    attempts: int = 3,
) -> tuple[str, float | None]:
    """Возвращает (текст, точная стоимость в рублях или None, если API её не отдал)."""
    url = env["base_url"].rstrip("/") + "/audio/transcriptions"

    # Чтение, base64 и сериализация — блокирующие, уводим в поток. Тело собираем
    # сами и держим ровно одну копию: на длинном файле каждая лишняя копия куска
    # это +3 МБ на каждый запрос в полёте.
    def build_body() -> bytes:
        payload = {
            "model": env["model"],
            "file": "data:audio/mp3;base64,"
            + base64.b64encode(chunk.read_bytes()).decode(),
            "response_format": "json",
        }
        if language and language != "auto":
            payload["language"] = language
        return json.dumps(payload).encode()

    body = await asyncio.to_thread(build_body)

    last_err = None
    for attempt in range(attempts):
        try:
            r = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {env['token']}",
                    "Content-Type": "application/json",
                },
                content=body,
            )
            r.raise_for_status()
            # обычно JSON, но при response_format=text приходит plain text
            if "application/json" not in r.headers.get("content-type", ""):
                text = r.text.strip()
                if not text:
                    raise RuntimeError("пустой ответ API")
                return text, None
            data = r.json()
            status = (data.get("status") or "").lower()
            if not data.get("text") and data.get("id") and (status in PENDING or not status):
                # асинхронная модель: результат забираем поллингом
                text, cost = await _poll_job(client, env, data["id"], on_wait)
            else:
                text, cost = _parse_result(data)
            if not text:
                raise RuntimeError("пустой ответ API")
            return text, cost
        except Exception as e:  # сеть/5xx — повторяем
            last_err = e
            if on_retry:  # именно detail, а не body — body это тело запроса
                detail = getattr(getattr(e, "response", None), "text", "") or ""
                on_retry(attempt + 1, f"{e} {detail[:120]}".strip())
            if attempt < attempts - 1:
                await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(
        f"{chunk.name}: не удалось транскрибировать: {last_err}"
    ) from last_err


async def run_all(
    chunks: list[Path], env: dict, language: str, jobs: int, dash
) -> tuple[list[str], list[str], list[float | None]]:
    """Все куски уходят в сеть одновременно, одним event loop, без потоков.

    jobs > 0 ограничивает число одновременных запросов. Возвращает
    (тексты по порядку кусков, имена упавших, стоимости по кускам).
    """
    from .console import rub  # локально, чтобы api не тянул UI на импорте

    texts: list[str] = [""] * len(chunks)
    costs: list[float | None] = [None] * len(chunks)
    failed: list[str] = []
    # jobs <= 0 — «все сразу», но с потолком: каждый запрос в полёте держит
    # ~3 МБ base64, и на десятичасовом файле 120 кусков съели бы под гигабайт
    limit = jobs if jobs > 0 else min(len(chunks), AUTO_JOBS)
    sem = asyncio.Semaphore(limit) if limit < len(chunks) else None

    async def one(chunk: Path):
        return await transcribe_chunk(
            client, chunk, env, language,
            on_retry=lambda n, m: dash.set(chunk.name, "work", f"повтор {n}/3: {m}"),
            on_wait=lambda sec: dash.set(chunk.name, "work", f"ждём результат… {sec:.0f} c"),
        )

    async def work(idx: int, chunk: Path):
        try:
            if sem:
                async with sem:  # пока ждём очереди, кусок числится «ожидает»
                    dash.set(chunk.name, "work")
                    text, cost = await one(chunk)
            else:
                dash.set(chunk.name, "work")
                text, cost = await one(chunk)
        except Exception as e:
            dash.set(chunk.name, "fail", str(e)[:120], advance=True)
            failed.append(chunk.name)
            return
        texts[idx], costs[idx] = text, cost
        note = f"{len(text.split())} слов" + (f" · {rub(cost)}" if cost is not None else "")
        dash.set(chunk.name, "done", note, advance=True)

    limits = httpx.Limits(max_connections=limit, max_keepalive_connections=limit)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(600.0, connect=30.0), limits=limits
    ) as client:
        await asyncio.gather(*(work(i, c) for i, c in enumerate(chunks)))
    return texts, failed, costs
