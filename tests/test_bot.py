import asyncio
import json

import pytest

from app.async_runtime import configure_asyncio_policy
from app.bot import require_catalog_admin, split_telegram_text, write_bot_heartbeat


def test_windows_async_policy_is_safe_to_configure() -> None:
    configure_asyncio_policy()


def test_long_telegram_report_is_split_safely() -> None:
    text = "\n".join(f"Строка {index}: " + "x" * 80 for index in range(100))

    chunks = split_telegram_text(text, limit=500)

    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "\n".join(chunks) == text


@pytest.mark.asyncio
async def test_bot_heartbeat_is_written_atomically(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    task = asyncio.create_task(write_bot_heartbeat(interval_seconds=1))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    payload = json.loads(
        (tmp_path / ".harness/runs/bot-heartbeat.json").read_text("utf-8")
    )
    assert payload["pid"] > 0
    assert payload["updated_at"]


@pytest.mark.asyncio
async def test_catalog_write_commands_fail_closed_without_admins(monkeypatch) -> None:
    class FakeSettings:
        @staticmethod
        def telegram_admin_id_set() -> set[int]:
            return set()

    class FakeMessage:
        from_user = type("User", (), {"id": 123})()

        def __init__(self) -> None:
            self.answers: list[str] = []

        async def answer(self, text: str) -> None:
            self.answers.append(text)

    monkeypatch.setattr("app.bot.get_settings", lambda: FakeSettings())
    message = FakeMessage()

    assert await require_catalog_admin(message) is False
    assert "TELEGRAM_ADMIN_IDS" in message.answers[0]
