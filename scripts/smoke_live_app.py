"""Smoke test the deployed Streamlit app end-to-end with a real browser.

Loads the app, sends one chat message, and verifies an assistant response
streams back. Streamlit is websocket-driven, so a plain HTTP check only proves
the page shell loads — this drives the actual chat round-trip (browser ->
Streamlit -> agent service -> LLM -> back).

Usage:
    uv run --with playwright python scripts/smoke_live_app.py [URL]

The URL defaults to the deployed app, or set LIVE_APP_URL. For local testing:
    uv run --with playwright python scripts/smoke_live_app.py http://localhost:8501

Requires a Chromium Playwright can find: either `playwright install chromium`,
or a pre-provisioned browser via PLAYWRIGHT_BROWSERS_PATH (as in Claude Code
cloud environments). Exits 0 on pass, 1 on fail, and writes
smoke_live_app_failure.png next to the CWD on failure for diagnosis.

Note: against the deployed app this sends one real message, which costs one
(cheap) LLM call. Streamlit Community Cloud apps that went to sleep are woken
by clicking through the wake-up screen; allow a couple of minutes for that.
"""

import os
import sys
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

DEFAULT_URL = "https://agent-service-toolkit.streamlit.app/"
# Claude Code 云环境中预装浏览器的固定符号链接，
# 当已安装的 Playwright 没有自带浏览器时，用它作为备用路径
CLOUD_CHROMIUM = "/opt/pw-browsers/chromium"
TEST_MESSAGE = "Reply with the single word: pong"
CHAT_INPUT_SELECTOR = '[data-testid="stChatInput"] textarea'
WAKE_TIMEOUT_S = 180
RESPONSE_TIMEOUT_S = 120
STREAM_SETTLE_S = 4


def log(msg: str) -> None:
    print(f"[smoke_live_app] {msg}", flush=True)


def _dump_testids(ctx, label: str) -> None:
    try:
        ids = ctx.locator("[data-testid]").evaluate_all(
            "els => Array.from(new Set(els.map(e => e.getAttribute('data-testid')))).slice(0, 40)"
        )
        log(f"data-testids in {label} ({len(ids)}): {ids}")
    except Exception as e:
        log(f"data-testids in {label} unavailable: {e}")


def dump_diagnostics(page) -> None:
    """Text diagnostics for a failed run, readable straight from the CI logs."""
    try:
        log(f"page title: {page.title()!r}  url: {page.url}")
    except Exception:
        pass
    n_iframes = page.locator("iframe").count()
    log(f"iframes present: {n_iframes}")
    _dump_testids(page, "top frame")
    for i in range(n_iframes):
        _dump_testids(page.frame_locator("iframe").nth(i), f"iframe[{i}]")
    try:
        body = (page.locator("body").inner_text(timeout=2_000) or "").strip()
        log(f"top-frame visible text ({len(body)} chars): {body[:600]!r}")
    except Exception:
        pass


def fail(page, reason: str) -> None:
    log(f"FAIL: {reason}")
    try:
        dump_diagnostics(page)
    except Exception as e:
        log(f"diagnostics unavailable: {e}")
    try:
        page.screenshot(path="smoke_live_app_failure.png", full_page=True)
        log("screenshot saved to smoke_live_app_failure.png")
    except Exception:
        pass
    sys.exit(1)


def wake_if_sleeping(page) -> None:
    """Streamlit Community Cloud shows a wake-up screen for slept apps."""
    wake_button = page.get_by_text("get this app back up", exact=False)
    try:
        wake_button.first.wait_for(state="visible", timeout=5_000)
    except PlaywrightTimeoutError:
        return  # 这里不是固定休眠，而是等待条件成立
    log("app is asleep - clicking wake-up button")
    wake_button.first.click()


def find_app_root(page, timeout_s: int):
    """Return the page or iframe context that holds the app's chat input.

    Local runs render the app at the top level; Streamlit Community Cloud wraps it
    in an iframe that page.locator() can't see into, so probe iframes as well.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if page.locator(CHAT_INPUT_SELECTOR).count():
            return page
        for i in range(page.locator("iframe").count()):
            frame = page.frame_locator("iframe").nth(i)
            if frame.locator(CHAT_INPUT_SELECTOR).count():
                return frame
        time.sleep(1)
    return None


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LIVE_APP_URL", DEFAULT_URL)
    log(f"target: {url}")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            executable = os.environ.get("CHROMIUM_EXECUTABLE", CLOUD_CHROMIUM)
            if not os.path.exists(executable):
                raise
            log(f"bundled browser missing - falling back to {executable}")
            browser = p.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        wake_if_sleeping(page)

        # 聊天输入框出现，说明 Streamlit 已启动、WebSocket 已连接，
        # 而且应用脚本已执行到初始化后的界面渲染步骤；先判断输入框
        # 位于顶层本地页面，还是 Community Cloud 的内嵌框架中，
        # 后续所有页面操作都应针对这个正确的上下文
        root = find_app_root(page, WAKE_TIMEOUT_S)
        if root is None:
            fail(page, f"chat input never appeared within {WAKE_TIMEOUT_S}s")
        log(f"app loaded, chat input visible ({'top-level' if root is page else 'iframe'})")

        chat_input = root.locator(CHAT_INPUT_SELECTOR)
        messages = root.locator('[data-testid="stChatMessage"]')
        # 欢迎语只在空对话中显示，发送消息并重新运行后会消失，
        # 因此应按文字内容检测，不能只按消息数量判断
        pre_send_last = (messages.last.inner_text() or "").strip() if messages.count() else ""

        chat_input.fill(TEST_MESSAGE)
        chat_input.press("Enter")
        log("message sent, waiting for assistant response")

        # 等待用户问题进入对话，后面出现一条最终助手回复，
        # 并确认回复非空、是新产生的、且已稳定不再流式变化
        deadline = time.monotonic() + RESPONSE_TIMEOUT_S
        last_text, stable_since = "", None
        while time.monotonic() < deadline:
            texts = [(t or "").strip() for t in messages.all_inner_texts()]
            if texts and any(TEST_MESSAGE in t for t in texts[:-1]):
                text = texts[-1]
                if text and text != TEST_MESSAGE and text != pre_send_last:
                    if text == last_text:
                        if stable_since and time.monotonic() - stable_since >= STREAM_SETTLE_S:
                            log(f"PASS: assistant responded ({len(text)} chars): {text[:120]!r}")
                            browser.close()
                            return
                    else:
                        last_text, stable_since = text, time.monotonic()
            time.sleep(1)

        fail(page, f"no stable assistant response within {RESPONSE_TIMEOUT_S}s")


if __name__ == "__main__":
    main()
