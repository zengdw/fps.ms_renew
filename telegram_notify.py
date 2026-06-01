"""Telegram notification for execution summary and screenshot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


@dataclass
class ExecutionReport:
    console_status: str = ""
    turnstile_status: str = ""
    screenshot: Path | None = None

    def to_caption(self) -> str:
        lines: list[str] = []
        if self.console_status:
            lines.append(f"Start按钮: {self.console_status}")
        if self.turnstile_status:
            lines.append(f"Turnstile 验证: {self.turnstile_status}")
        return "\n".join(lines) or "无状态信息"


class TelegramNotifier:
    """Send execution summary with screenshot via Bot API."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self.token = (token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = (chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _post(self, method: str, **kwargs) -> None:
        url = TELEGRAM_API.format(token=self.token, method=method)
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, **kwargs)
            resp.raise_for_status()

    def send_execution_report(self, report: ExecutionReport) -> None:
        if not self.enabled:
            return

        caption = report.to_caption()
        if report.screenshot and report.screenshot.is_file():
            with report.screenshot.open("rb") as photo:
                self._post(
                    "sendPhoto",
                    data={"chat_id": self.chat_id, "caption": caption[:1024]},
                    files={"photo": (report.screenshot.name, photo, "image/png")},
                )
        else:
            self._post(
                "sendMessage",
                json={"chat_id": self.chat_id, "text": caption},
            )
