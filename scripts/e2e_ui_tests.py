"""Browser end-to-end scenarios for the Streamlit app UI.

Extends ``scripts/smoke_live_app.py`` (a single chat round-trip) with a small
suite covering the key user journeys that Streamlit version bumps or client /
schema changes have quietly broken before. It drives a real browser through the
app the same way a user would, so it catches breakage that the pytest suite
(which mocks the transport) and the docker CI health checks cannot.

Usage:
    uv run --with playwright python scripts/e2e_ui_tests.py [URL] [scenario ...]

Run everything (the default), or only named scenarios:
    uv run --with playwright python scripts/e2e_ui_tests.py            # all, vs localhost
    uv run --with playwright python scripts/e2e_ui_tests.py chat feedback
    uv run --with playwright python scripts/e2e_ui_tests.py https://my-app.example.com
    uv run --with playwright python scripts/e2e_ui_tests.py --list

The scenarios don't hardcode an app URL - they take it as an argument - so the
same suite works against any deployment. It defaults to a locally running app
(http://localhost:8501); pass a different URL (or set ``LIVE_APP_URL``) to point
it at a deployed one. So one suite serves both: run it against a local
``USE_FAKE_MODEL=true`` service + ``streamlit run`` before a PR, and against the
deployed URL to check production.

The default scenarios use the ``fake`` model, so a local run needs no API keys
and makes no real LLM calls. To sanity-check a real model end to end, run the
opt-in ``live_model`` scenario with ``--model=<name>`` (or ``E2E_LIVE_MODEL``);
it selects that model in Settings and sends one short prompt (one cheap LLM call):
    uv run --with playwright python scripts/e2e_ui_tests.py --model=gpt-5-nano live_model

Notes:
  - Scenarios send only short prompts, so even a live run stays cheap.
  - The feedback scenario verifies the widget renders and is interactive (the part
    a Streamlit bump breaks) but does not submit a rating - clicking a star writes
    to LangSmith through the backend, which would pollute the production project on
    every monitoring run. (Nothing else in the suite touches LangSmith: plain
    chat/history don't trace unless tracing is explicitly enabled.)

Requires a Chromium Playwright can find: either ``playwright install chromium``,
or a pre-provisioned browser via PLAYWRIGHT_BROWSERS_PATH (as in Claude Code
cloud environments). Exits 0 if every selected scenario passes, 1 otherwise, and
writes ``e2e_<scenario>_failure.png`` next to the CWD for any scenario that fails.
"""

import os
import sys
import time
import urllib.parse

from playwright.sync_api import Browser, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

DEFAULT_URL = "http://localhost:8501"
# Claude Code 云环境中预装浏览器的固定符号链接，
# 当已安装的 Playwright 没有自带浏览器时，用它作为备用路径
CLOUD_CHROMIUM = "/opt/pw-browsers/chromium"

# 简单且不调用工具的智能体可以使消息数量固定，每轮恰好生成一条
# 助手消息，这样无论使用什么模型，按消息数量等待都比较可靠；
# 需要验证智能体选择的场景会覆盖此默认值
CHAT_AGENT = "chatbot"

# 恢复场景要求完整多轮历史能通过检查点保存并重新读取；
# @entrypoint 风格的聊天机器人通过 /history 仅暴露最后一条回复，
# 因此这里改用消息通道能够累计每轮消息的 StateGraph 智能体
HISTORY_AGENT = "research-assistant"

WAKE_TIMEOUT_S = 180
RESPONSE_TIMEOUT_S = 120
STREAM_SETTLE_S = 4

CHAT_INPUT = '[data-testid="stChatInput"] textarea'
CHAT_MESSAGE = '[data-testid="stChatMessage"]'


class E2EError(Exception):
    """A scenario assertion failed."""


def log(msg: str) -> None:
    print(f"[e2e] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# 浏览器和应用辅助函数
# --------------------------------------------------------------------------- #
def launch_browser(p) -> Browser:
    try:
        return p.chromium.launch()
    except Exception:
        executable = os.environ.get("CHROMIUM_EXECUTABLE", CLOUD_CHROMIUM)
        if not os.path.exists(executable):
            raise
        log(f"bundled browser missing - falling back to {executable}")
        return p.chromium.launch(executable_path=executable)


def build_url(base_url: str, **params: str) -> str:
    """Merge query params into base_url, preserving any it already carries."""
    parts = urllib.parse.urlsplit(base_url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query.update({k: v for k, v in params.items() if v is not None})
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def wake_if_sleeping(page: Page) -> None:
    """Streamlit Community Cloud shows a wake-up screen for slept apps."""
    wake_button = page.get_by_text("get this app back up", exact=False)
    try:
        wake_button.first.wait_for(state="visible", timeout=5_000)
    except PlaywrightTimeoutError:
        return  # 这里不是固定休眠，而是等待条件成立
    log("app is asleep - clicking wake-up button")
    wake_button.first.click()


def open_app(browser: Browser, url: str, agent: str | None = None) -> Page:
    """Open a fresh browser context on the app and wait until it is interactive."""
    if agent:
        url = build_url(url, agent=agent)
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    wake_if_sleeping(page)
    # 聊天输入框出现，说明 Streamlit 已启动、WebSocket 已连接，
    # 而且应用脚本已执行到智能体和模型初始化之后的界面渲染步骤
    page.locator(CHAT_INPUT).wait_for(state="visible", timeout=WAKE_TIMEOUT_S * 1_000)
    return page


def query_param(page: Page, key: str) -> str | None:
    query = urllib.parse.urlsplit(page.url).query
    return dict(urllib.parse.parse_qsl(query)).get(key)


def message_texts(page: Page) -> list[str]:
    return [(t or "").strip() for t in page.locator(CHAT_MESSAGE).all_inner_texts()]


def send_message(page: Page, text: str) -> None:
    chat_input = page.locator(CHAT_INPUT)
    chat_input.fill(text)
    chat_input.press("Enter")


def wait_for_response(
    page: Page,
    prompt: str,
    min_count: int,
    timeout_s: int = RESPONSE_TIMEOUT_S,
) -> str:
    """Wait for a new assistant reply to `prompt` to appear and stop streaming.

    Uses the message count (not text) to detect a new turn, since the fake model
    replies with identical text every turn. The reply is considered done once the
    last message is non-empty, is not the prompt itself, and stops changing for
    STREAM_SETTLE_S.
    """
    deadline = time.monotonic() + timeout_s
    last_text, stable_since = "", None
    while time.monotonic() < deadline:
        texts = message_texts(page)
        # 开始读取最后一条回复前，必须确认用户问题已经作为历史消息出现，
        # 并且它后面已有助手回复
        if len(texts) >= min_count and any(prompt in t for t in texts[:-1]):
            text = texts[-1]
            if text and text != prompt:
                if text == last_text:
                    if stable_since and time.monotonic() - stable_since >= STREAM_SETTLE_S:
                        return text
                else:
                    last_text, stable_since = text, time.monotonic()
        time.sleep(1)
    raise E2EError(f"no stable assistant reply to {prompt!r} (>= {min_count} msgs) in {timeout_s}s")


def open_settings(page: Page) -> None:
    """Open the Settings popover. It stays open across reruns, so open it once and
    do all settings interactions before dismissing it - re-clicking closes it."""
    page.get_by_role("button", name="Settings").first.click()
    page.locator('[data-testid="stSelectbox"]').first.wait_for(state="visible", timeout=15_000)


def selectbox_value(page: Page, label: str) -> str | None:
    box = page.locator('[data-testid="stSelectbox"]').filter(has_text=label)
    return box.get_by_role("combobox").get_attribute("value")


# --------------------------------------------------------------------------- #
# 测试场景
# --------------------------------------------------------------------------- #
def scenario_chat(browser: Browser, base_url: str) -> None:
    """Baseline: one message in, one assistant reply streams back and settles."""
    page = open_app(browser, base_url, agent=CHAT_AGENT)
    prompt = "Reply with the single word: pong"
    send_message(page, prompt)
    reply = wait_for_response(page, prompt, min_count=2)
    log(f"assistant replied ({len(reply)} chars): {reply[:80]!r}")


def scenario_multi_turn_resume(browser: Browser, base_url: str) -> None:
    """Two-turn conversation, then resume it from the Share link in a fresh session.

    Exercises thread persistence, the agent-aware /history fetch on resume, and the
    Share/resume dialog - the dialog regression that #330 fixed would fail here
    because building the share URL would error instead of rendering a link.
    """
    turn1 = "First turn: remember the number 7"
    turn2 = "Second turn: what number did I mention?"
    page = open_app(browser, base_url, agent=HISTORY_AGENT)
    thread_id = query_param(page, "thread_id")
    if not thread_id:
        raise E2EError("thread_id was not published to the URL on load")

    send_message(page, turn1)
    wait_for_response(page, turn1, min_count=2)
    send_message(page, turn2)
    wait_for_response(page, turn2, min_count=4)

    # 打开分享或恢复对话的弹窗，读取它生成的可分享网址；
    # 弹窗框架会先出现，随后 Streamlit 才填入 Markdown 内容，
    # 因此应等待代码块文字出现，而不是弹窗刚打开就立即读取
    page.get_by_role("button", name="Share/resume chat").first.click()
    dialog = page.locator('[role="dialog"]')
    dialog.wait_for(state="visible", timeout=15_000)
    code = dialog.locator('[data-testid="stCode"] code')
    share_url = ""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if code.count() and (share_url := code.first.inner_text().strip()):
            break
        page.wait_for_timeout(500)
    if not share_url:
        raise E2EError("Share/resume dialog rendered no chat URL (share_chat_dialog broken?)")
    if thread_id not in share_url or "agent=" not in share_url:
        raise E2EError(f"share URL missing thread_id/agent: {share_url!r}")
    log(f"share URL: {share_url}")

    # 在全新会话中恢复对话，不复用原会话状态，并确认历史已重新加载；
    # StateGraph 智能体会持久保存每轮消息，所以应重放之前的
    # 两个问题；如果恢复失败成了空对话，则只会看到
    # 智能体的欢迎消息
    resumed = open_app(browser, share_url)
    if query_param(resumed, "thread_id") != thread_id:
        raise E2EError("resumed session did not carry the original thread_id from the share URL")
    joined = "\n".join(message_texts(resumed))
    for needle in (turn1, turn2):
        if needle not in joined:
            raise E2EError(f"resumed thread did not replay {needle!r} - history not restored")
    log(f"resumed thread {thread_id} replayed both prior turns from history")


def scenario_settings_selectors(browser: Browser, base_url: str) -> None:
    """Settings popover: the model + agent selectboxes render, and switching the
    agent to a non-default one syncs it into the ?agent= URL param."""
    page = open_app(browser, base_url)  # 默认使用默认智能体，因此网址最初没有 ?agent= 参数
    open_settings(page)

    model = selectbox_value(page, "LLM to use")
    if not model:
        raise E2EError("LLM selectbox rendered without a selected model")
    log(f"model selectbox shows: {model!r}")

    default_agent = selectbox_value(page, "Agent to use")
    agents = page.locator('[data-testid="stSelectbox"]').filter(has_text="Agent to use")
    agents.get_by_role("combobox").click()
    options = page.locator('[role="option"]')
    options.first.wait_for(state="visible", timeout=10_000)
    all_agents = [options.nth(i).inner_text().strip() for i in range(options.count())]
    if len(all_agents) < 2:
        raise E2EError(f"expected multiple agents to choose from, saw {all_agents}")
    # 选择任意非默认智能体；默认值不会写入网址，
    # 所以切换到非默认项才能证明查询参数绑定有效
    target = next(a for a in all_agents if a != default_agent)
    options.filter(has_text=target).first.click()
    page.wait_for_timeout(1_500)

    if selectbox_value(page, "Agent to use") != target:
        raise E2EError(f"agent selectbox did not switch to {target!r}")
    if query_param(page, "agent") != target:
        raise E2EError(f"?agent= URL param is {query_param(page, 'agent')!r}, expected {target!r}")
    log(f"agent switched {default_agent!r} -> {target!r} and synced to the URL")


def scenario_feedback(browser: Browser, base_url: str) -> None:
    """After a reply, the star feedback widget renders and is interactive.

    Asserts the widget's structure - the part a Streamlit bump breaks - without
    submitting a rating: clicking a star writes to LangSmith via the backend, and
    doing that on every run would pollute the production LangSmith project during
    monitoring (and hang against a backend that can't reach LangSmith). We verify
    the stars render, carry their expected aria-labels, and are enabled/clickable.
    """
    page = open_app(browser, base_url, agent=CHAT_AGENT)
    prompt = "Reply with the single word: pong"
    send_message(page, prompt)
    wait_for_response(page, prompt, min_count=2)

    widget = page.locator('[data-testid="stFeedback"]').last
    widget.wait_for(state="visible", timeout=15_000)
    stars = widget.locator('[data-testid="stFeedbackButton"]')
    if stars.count() != 5:
        raise E2EError(f"expected a 5-star feedback widget, found {stars.count()} stars")
    labels = [stars.nth(i).get_attribute("aria-label") for i in range(5)]
    if labels != [f"{i} out of 5 stars" for i in range(1, 6)]:
        raise E2EError(f"feedback stars have unexpected aria-labels: {labels}")
    if not stars.first.is_enabled() or not stars.last.is_enabled():
        raise E2EError("feedback stars rendered but are not interactive")
    log("5-star feedback widget rendered with expected labels and is interactive")


def scenario_streaming_toggle(browser: Browser, base_url: str) -> None:
    """Turning off 'Stream results' still produces a reply via the non-streaming
    (ainvoke) path - a code path the default streaming run never exercises."""
    page = open_app(browser, base_url, agent=CHAT_AGENT)
    open_settings(page)
    toggle = page.locator('[data-testid="stCheckbox"]').filter(has_text="Stream results")
    toggle.wait_for(state="visible", timeout=10_000)
    toggle.click()  # 默认已开启，此处将其关闭
    page.keyboard.press("Escape")  # 关闭弹出面板，使聊天输入框可以操作
    page.wait_for_timeout(500)

    prompt = "Reply with the single word: pong"
    send_message(page, prompt)
    reply = wait_for_response(page, prompt, min_count=2)
    log(f"non-streaming reply rendered ({len(reply)} chars)")


def scenario_new_chat(browser: Browser, base_url: str) -> None:
    """'New Chat' starts a fresh thread: a new thread_id in the URL and a cleared
    conversation."""
    page = open_app(browser, base_url, agent=CHAT_AGENT)
    prompt = "Reply with the single word: pong"
    send_message(page, prompt)
    wait_for_response(page, prompt, min_count=2)
    old_thread = query_param(page, "thread_id")

    page.get_by_role("button", name="New Chat").first.click()
    page.locator(CHAT_INPUT).wait_for(state="visible", timeout=30_000)

    deadline = time.monotonic() + 15
    while query_param(page, "thread_id") == old_thread and time.monotonic() < deadline:
        page.wait_for_timeout(500)
    new_thread = query_param(page, "thread_id")
    if not new_thread or new_thread == old_thread:
        raise E2EError(f"thread_id did not change on New Chat (still {old_thread!r})")
    if any(prompt in t for t in message_texts(page)):
        raise E2EError("previous conversation was not cleared after New Chat")
    log(f"New Chat reset thread {old_thread} -> {new_thread} and cleared the conversation")


def scenario_live_model(browser: Browser, base_url: str) -> None:
    """Opt-in: select a real model in Settings and confirm it answers end to end.

    Unlike the other scenarios (which run on the ``fake`` model), this exercises a
    live LLM, so it needs a backend that offers the model and the credentials for
    it. Name the model with ``--model=<name>`` or ``E2E_LIVE_MODEL``. It sends one
    short prompt - a single cheap LLM call - and checks the reply is a real one,
    not the fake-model placeholder.
    """
    model = os.environ.get("E2E_LIVE_MODEL", "").strip()
    if not model:
        raise E2EError("live_model needs a model - pass --model=<name> or set E2E_LIVE_MODEL")
    page = open_app(browser, base_url, agent=CHAT_AGENT)
    open_settings(page)
    box = page.locator('[data-testid="stSelectbox"]').filter(has_text="LLM to use")
    box.get_by_role("combobox").click()
    option = page.get_by_role("option", name=model, exact=True)
    if not option.count():
        available = page.locator('[role="option"]').all_inner_texts()
        raise E2EError(f"model {model!r} is not offered by this app; available: {available}")
    option.first.click()
    page.keyboard.press("Escape")  # 关闭弹出面板，使聊天输入框可以操作
    page.wait_for_timeout(500)

    prompt = "Reply with only the word: pong"
    send_message(page, prompt)
    reply = wait_for_response(page, prompt, min_count=2)
    if "fake model" in reply.lower():
        raise E2EError(f"expected a reply from {model!r} but got the fake-model placeholder")
    log(f"live model {model!r} replied ({len(reply)} chars): {reply[:80]!r}")


# 默认测试使用模拟模型，不需要接口密钥；live_model 是
# 显式启用的真实模型测试，只有指定名称或传入 --model 时才运行
SCENARIOS = {
    "chat": scenario_chat,
    "multi_turn_resume": scenario_multi_turn_resume,
    "settings_selectors": scenario_settings_selectors,
    "feedback": scenario_feedback,
    "streaming_toggle": scenario_streaming_toggle,
    "new_chat": scenario_new_chat,
    "live_model": scenario_live_model,
}
DEFAULT_SCENARIOS = [name for name in SCENARIOS if name != "live_model"]


# --------------------------------------------------------------------------- #
# 测试执行器
# --------------------------------------------------------------------------- #
def main() -> None:
    args = sys.argv[1:]
    if "--list" in args:
        print("\n".join(SCENARIOS))
        return

    url = os.environ.get("LIVE_APP_URL", DEFAULT_URL)
    names = []
    for arg in args:
        if arg in SCENARIOS:
            names.append(arg)
        elif arg.startswith("--model="):
            os.environ["E2E_LIVE_MODEL"] = arg.split("=", 1)[1]
        elif arg.startswith(("http://", "https://")):
            url = arg
        else:
            print(f"unknown argument: {arg!r} (scenarios: {', '.join(SCENARIOS)})")
            sys.exit(2)
    if not names:
        # 默认运行模拟模型测试；只有设置模型时才额外运行 live_model
        names = list(DEFAULT_SCENARIOS)
        if os.environ.get("E2E_LIVE_MODEL"):
            names.append("live_model")

    log(f"target: {url}")
    log(f"scenarios: {', '.join(names)}")

    results: dict[str, str] = {}
    with sync_playwright() as p:
        browser = launch_browser(p)
        for name in names:
            log(f"--- {name} ---")
            start = time.monotonic()
            try:
                SCENARIOS[name](browser, url)
                results[name] = "PASS"
                log(f"PASS: {name} ({time.monotonic() - start:.0f}s)")
            except Exception as e:
                results[name] = f"FAIL: {e}"
                log(f"FAIL: {name}: {e}")
                _screenshot_failure(browser, name)
        browser.close()

    log("=" * 60)
    for name in names:
        log(f"{results[name].split(':')[0]:<4} {name}: {results[name]}")
    failed = [n for n, r in results.items() if not r.startswith("PASS")]
    if failed:
        log(f"{len(failed)} of {len(names)} scenario(s) failed: {', '.join(failed)}")
        sys.exit(1)
    log(f"all {len(names)} scenario(s) passed")


def _screenshot_failure(browser: Browser, name: str) -> None:
    """Save a screenshot of the last open page for a failed scenario."""
    path = f"e2e_{name}_failure.png"
    try:
        contexts = browser.contexts
        if contexts and contexts[-1].pages:
            contexts[-1].pages[-1].screenshot(path=path, full_page=True)
            log(f"screenshot saved to {path}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
