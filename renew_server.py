#!/usr/bin/env python3
"""FPS.ms server renewal automation."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from telegram_notify import ExecutionReport, TelegramNotifier

DEFAULT_SERVER_URL = (
    "https://panel.fps.ms/server/69f56310-b3db-47b8-b38d-cc1bf7c5a664"
)
DEFAULT_CONSOLE_BTN_SELECTOR = (
    "div.ServerConsoleContainer__ServerContainer-sc-1jk042-0.jSQyTR.mb-4 > "
    "div.col-span-4.sm\\:col-span-2.lg\\:col-span-1.relative.z-10.mt-2.sm\\:mt-0 > "
    "div > button:nth-child(1)"
)
ARTIFACTS_DIR = Path("artifacts")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        logger.error("缺少必填环境变量: %s", name)
        sys.exit(1)
    return value

def parse_cookie_header(header: str) -> list[dict]:
    cookies: list[dict] = []
    seen: set[str] = set()

    for part in header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        seen.add(name)
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": ".fps.ms",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }
        )

    return cookies


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def save_screenshot(page, filename: str) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / filename
    page.screenshot(path=str(path), full_page=True)
    logger.info("已保存截图: %s", path)
    return path


def click_console_button(page, selector: str, timeout_ms: int) -> str:
    btn = page.locator(selector)
    btn.wait_for(state="visible", timeout=timeout_ms)

    has_disabled = btn.get_attribute("disabled") is not None
    is_enabled = btn.is_enabled()
    logger.info(
        "控制台按钮状态: disabled属性=%s, is_enabled=%s",
        has_disabled,
        is_enabled,
    )

    if not has_disabled and is_enabled:
        btn.click()
        logger.info("已点击控制台控制按钮")
        action = "已点击"
    else:
        logger.info("控制台按钮已禁用（disabled 属性），跳过")
        action = "已禁用，跳过"

    return f"disabled={has_disabled}, is_enabled={is_enabled}, {action}"


def click_renew_button(page, timeout_ms: int) -> None:
    renew = page.locator("button#renewserver")
    renew.wait_for(state="visible", timeout=timeout_ms)
    renew.click()
    logger.info("已点击续期按钮 button#renewserver")


def try_click_turnstile_checkbox(page) -> bool:
    for frame in page.frames:
        url = frame.url or ""
        if "challenges.cloudflare.com" not in url and "turnstile" not in url.lower():
            continue
        for sel in ('input[type="checkbox"]', '[role="checkbox"]', "label"):
            try:
                target = frame.locator(sel).first
                if target.count() > 0 and target.is_visible():
                    target.click(timeout=3000)
                    logger.info("已点击 Turnstile checkbox")
                    return True
            except Exception:
                continue
    return False


def wait_turnstile_after_renew(page, timeout_ms: int) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_ms / 1000
    widget_seen = False
    last_click = 0.0

    while time.monotonic() < deadline:
        security_text = page.locator("text=Please complete the security check")
        turnstile_iframe = page.locator(
            'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]'
        )
        turnstile_response = page.locator('input[name="cf-turnstile-response"]')

        if security_text.count() > 0 or turnstile_iframe.count() > 0:
            widget_seen = True

        if turnstile_response.count() > 0:
            value = turnstile_response.first.input_value(timeout=1000)
            if value:
                status = "已通过"
                logger.info("Turnstile 验证已通过（cf-turnstile-response 有值）")
                return True, status

        if widget_seen and security_text.count() == 0 and turnstile_iframe.count() == 0:
            status = "已通过（小组件已消失）"
            logger.info("Turnstile 小组件已消失，视为验证完成")
            return True, status

        if widget_seen and time.monotonic() - last_click > 5:
            try_click_turnstile_checkbox(page)
            last_click = time.monotonic()

        page.wait_for_timeout(500)

    logger.error("Turnstile 验证超时")
    return False, "超时"


def main() -> None:
    load_dotenv()

    cookie_header = require_env("COOKIE")
    server_url = os.environ.get("SERVER_URL", DEFAULT_SERVER_URL).strip()
    console_selector = os.environ.get(
        "CONSOLE_BTN_SELECTOR", DEFAULT_CONSOLE_BTN_SELECTOR
    ).strip()
    headless = env_bool("HEADLESS", False)
    timeout_ms = int(os.environ.get("TIMEOUT_MS", "120000"))

    cookies = parse_cookie_header(cookie_header)
    report = ExecutionReport()
    context = None

    from cloakbrowser import launch_context

    logger.info("启动浏览器 (headless=%s)", headless)
    context = launch_context(headless=headless)
    context.add_cookies(cookies)

    page = context.new_page()
    try:
        logger.info("打开页面: %s", server_url)
        page.goto(server_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.locator("#app").wait_for(state="visible", timeout=timeout_ms)
        logger.info("页面加载完成")

        report.console_status = click_console_button(
            page, console_selector, timeout_ms
        )
        click_renew_button(page, timeout_ms)

        turnstile_ok, report.turnstile_status = wait_turnstile_after_renew(
            page, timeout_ms
        )
        if turnstile_ok:
            logger.info("续期流程完成")
            report.screenshot = save_screenshot(page, "renew_success.png")
        else:
            logger.error("续期流程未完成（Turnstile 超时）")
            report.screenshot = save_screenshot(page, "renew_cf_timeout.png")
            sys.exit(1)
    except Exception:
        logger.exception("执行失败")
        report.turnstile_status = report.turnstile_status or "执行失败"
        try:
            report.screenshot = save_screenshot(page, "error.png")
        except Exception:
            pass
        sys.exit(1)
    finally:
        if context is not None:
            context.close()
        notifier = TelegramNotifier()
        if notifier.enabled:
            try:
                notifier.send_execution_report(report)
                logger.info("已发送 Telegram 通知")
            except Exception:
                logger.exception("Telegram 通知发送失败")


if __name__ == "__main__":
    main()
