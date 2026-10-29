import asyncio
import os
import urllib.parse
import uuid
from collections.abc import AsyncGenerator

import streamlit as st
from dotenv import load_dotenv
from pydantic import ValidationError

from client import AgentClient, AgentClientError
from schema import ChatHistory, ChatMessage, UserThreads
from schema.task_data import TaskData, TaskDataStatus
from voice import VoiceManager
from shipping_ui import draw_shipping_console

# 这个 Streamlit 应用提供简单聊天界面，用于与 LangGraph 智能体交互
# 应用有三个主要函数，它们都采用异步方式执行：

# - main()：初始化 Streamlit 应用及整体布局
# - draw_messages()：绘制聊天消息，既可以重放历史消息，
# 也可以显示新产生的流式消息
# - handle_feedback()：绘制反馈控件，记录用户反馈

# 应用主要通过 AgentClient 调用智能体的 FastAPI 接口


APP_TITLE = "CommerceDesk 智能客服"
APP_ICON = "🧰"
USER_ID_COOKIE = "user_id"


def get_or_create_user_id() -> str:
    """Get the user ID from session state or URL parameters, or create a new one if it doesn't exist."""
    # 检查会话状态中是否已有 user_id
    if USER_ID_COOKIE in st.session_state:
        return st.session_state[USER_ID_COOKIE]

    # 尝试通过 st.query_params 从网址参数中获取用户编号
    if USER_ID_COOKIE in st.query_params:
        user_id = st.query_params[USER_ID_COOKIE]
        st.session_state[USER_ID_COOKIE] = user_id
        return user_id

    # 没有找到时，生成新的 user_id
    user_id = str(uuid.uuid4())

    # 保存在本次网页会话的状态中
    st.session_state[USER_ID_COOKIE] = user_id

    # 同时写入网址参数，便于收藏或分享链接
    st.query_params[USER_ID_COOKIE] = user_id

    return user_id


@st.cache_data(ttl=600, show_spinner=False)
def fetch_user_threads_cached(
    base_url: str, user_id: str, agent_id: str | None = None, limit: int = 20
) -> UserThreads:
    """
    Fetch and cache user threads using the new synchronous get_user_threads method.
    """
    client = AgentClient(base_url=base_url, get_info=False)
    return client.get_user_threads(user_id=user_id, agent=agent_id, limit=limit)


async def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon=APP_ICON,
        menu_items={},
    )

    # 隐藏 Streamlit 右上角的界面控件
    st.html(
        """
        <style>
        [data-testid="stStatusWidget"] {
                visibility: hidden;
                height: 0%;
                position: fixed;
            }
        </style>
        """,
    )
    if st.get_option("client.toolbarMode") != "minimal":
        st.set_option("client.toolbarMode", "minimal")
        await asyncio.sleep(0.1)
        st.rerun()

    # 获取或创建用户编号
    user_id = get_or_create_user_id()

    if "agent_client" not in st.session_state:
        load_dotenv()
        agent_url = os.getenv("AGENT_URL")
        if not agent_url:
            host = os.getenv("HOST", "0.0.0.0")
            port = os.getenv("PORT", 8080)
            agent_url = f"http://{host}:{port}"
        try:
            with st.spinner("Connecting to agent service..."):
                st.session_state.agent_client = AgentClient(base_url=agent_url)
        except AgentClientError as e:
            st.error(f"Error connecting to agent service at {agent_url}: {e}")
            st.markdown("The service might be booting up. Try again in a few seconds.")
            st.stop()
    agent_client: AgentClient = st.session_state.agent_client

    agent_client.auth_secret = os.getenv("AUTH_SECRET")
    draw_shipping_console(agent_client.base_url, agent_client.auth_secret)

    # 初始化语音管理器，每个会话仅初始化一次
    if "voice_manager" not in st.session_state:
        st.session_state.voice_manager = VoiceManager.from_env()
    voice = st.session_state.voice_manager

    if "thread_id" not in st.session_state:
        thread_id = st.query_params.get("thread_id")
        if not thread_id:
            thread_id = str(uuid.uuid4())
            messages = []
        else:
            # 从网址中读取智能体名称，确保通过创建该对话的执行图
            # 读取对应的历史消息
            resume_agent = st.query_params.get("agent") or agent_client.agent
            try:
                messages: ChatHistory = agent_client.get_history(
                    thread_id=thread_id, agent=resume_agent
                ).messages
            except AgentClientError:
                st.error("No message history found for this Thread ID.")
                messages = []
        st.session_state.messages = messages
        st.session_state.thread_id = thread_id

    # 将 thread_id 保留在网址中，便于直接分享地址栏链接
    st.query_params["thread_id"] = st.session_state.thread_id

    # 配置选项
    with st.sidebar:
        st.header(f"{APP_ICON} {APP_TITLE}")

        ""
        "订单查询 · 政策问答 · 催单申请 · 人工审批"
        ""

        if st.button(":material/chat: New Chat", use_container_width=True):
            st.session_state.messages = []
            st.session_state.thread_id = str(uuid.uuid4())
            # 开始新对话时清除已保存的音频
            if "last_audio" in st.session_state:
                del st.session_state.last_audio
            st.rerun()

        with st.expander(":material/history: Previous Chats", expanded=False):
            try:
                url_agent = st.query_params.get("agent")
                if url_agent == "supportpilot":
                    agent_client.agent = url_agent
                else:
                    agent_client.agent = agent_client.info.default_agent
                user_threads = fetch_user_threads_cached(
                    base_url=agent_client.base_url,
                    user_id=user_id,
                    agent_id=agent_client.agent,
                    limit=20,
                )
                thread_list = user_threads.threads
            except Exception as e:
                st.caption(f"Couldn't load conversation history: {e}")
                thread_list = []

            for t in thread_list:
                label = t.title or f"Chat {t.thread_id[:8]}"
                if st.button(label, key=f"thread_{t.thread_id}", use_container_width=True):
                    try:
                        history: ChatHistory = agent_client.get_history(
                            thread_id=t.thread_id, agent=t.agent_id
                        )
                    except AgentClientError:
                        st.error("Could not load that conversation.")
                        continue
                    st.session_state.messages = history.messages
                    st.session_state.thread_id = t.thread_id
                    st.query_params["thread_id"] = t.thread_id
                    if "last_audio" in st.session_state:
                        del st.session_state.last_audio
                    st.rerun()

        with st.popover(":material/settings: Settings", use_container_width=True):
            model_idx = agent_client.info.models.index(agent_client.info.default_model)
            model = st.selectbox("LLM to use", options=agent_client.info.models, index=model_idx)
            agent_list = [entry.key for entry in agent_client.info.agents]
            agent_idx = agent_list.index(agent_client.info.default_agent)
            # 将选择结果同步到网址的 ?agent= 参数；选择默认智能体时省略该参数
            agent_client.agent = st.selectbox(
                "Agent to use",
                options=agent_list,
                index=agent_idx,
                key="agent",
                bind="query-params",
                on_change=fetch_user_threads_cached.clear,
            )
            use_streaming = st.toggle("Stream results", value=True)
            # 音频开关的回调：关闭时清除缓存音频
            enable_audio = st.toggle(
                "Enable audio generation",
                value=True,
                disabled=not voice or not voice.tts,
                help="Configure VOICE_TTS_PROVIDER in .env to enable"
                if not voice or not voice.tts
                else None,
                on_change=lambda: (
                    st.session_state.pop("last_audio", None)
                    if not st.session_state.get("enable_audio", True)
                    else None
                ),
                key="enable_audio",
            )

            # 显示用户编号，便于调试或向用户展示
            st.text_input("User ID (read-only)", value=user_id, disabled=True)

        @st.dialog("Share/resume chat")
        def share_chat_dialog() -> None:
            # st.context.url 是不带查询参数的浏览器网址；重新拼接
            # 参数并包含智能体名称，以便通过正确的图恢复对话
            if not st.context.url:
                st.error("Could not determine the app URL to build a shareable link.")
                return
            query = urllib.parse.urlencode(
                {
                    "thread_id": st.session_state.thread_id,
                    "agent": agent_client.agent,
                    USER_ID_COOKIE: user_id,
                }
            )
            chat_url = f"{st.context.url}?{query}"
            st.markdown(f"**Chat URL:**\n```text\n{chat_url}\n```")
            st.info("Copy the above URL to share or revisit this chat")

        if st.button(":material/upload: Share/resume chat", use_container_width=True):
            share_chat_dialog()

    # 绘制已有消息
    messages: list[ChatMessage] = st.session_state.messages

    if len(messages) == 0:
        WELCOME = "你好，我是 CommerceDesk。你可以查询订单、咨询商城政策，或提出催发货和加急处理申请。"

        with st.chat_message("ai"):
            st.write(WELCOME)

    # draw_messages() 接收一个异步消息迭代器
    async def amessage_iter() -> AsyncGenerator[ChatMessage, None]:
        for m in messages:
            yield m

    await draw_messages(amessage_iter())

    # 如果存在已保存音频，展示最后一条模型消息的音频
    # 这样可以在 st.rerun() 重新运行页面后保留音频
    if (
        voice
        and enable_audio
        and "last_audio" in st.session_state
        and st.session_state.last_message
        and len(messages) > 0
        and messages[-1].type == "ai"
    ):
        with st.session_state.last_message:
            audio_data = st.session_state.last_audio
            st.audio(audio_data["data"], format=audio_data["format"])

    # 用户提供新输入时，生成新消息
    # 优先使用可用的语音管理器，否则回退到普通文本输入
    # 启用语音必须配置 VOICE_STT_PROVIDER、VOICE_TTS_PROVIDER、OPENAI_API_KEY，
    # 这些配置应放在网页应用的 .env 中，而不是服务端的 .env
    if voice:
        user_input = voice.get_chat_input()
    else:
        user_input = st.chat_input()

    if user_input:
        is_first_message = len(messages) == 0
        messages.append(ChatMessage(type="human", content=user_input))
        st.chat_message("human").write(user_input)
        try:
            if use_streaming:
                stream = agent_client.astream(
                    message=user_input,
                    model=model,
                    thread_id=st.session_state.thread_id,
                    user_id=user_id,
                )
                await draw_messages(stream, is_new=True)
                # 为流式回复生成文本转语音音频
                # 注意：draw_messages() 会将最终消息保存在 st.session_state.messages 中，
                # 并将容器引用保存在 st.session_state.last_message 中
                if voice and enable_audio and st.session_state.messages:
                    last_msg = st.session_state.messages[-1]
                    # 只为有正文内容的模型回复生成音频
                    if last_msg.type == "ai" and last_msg.content:
                        # 文本已经由 draw_messages() 流式显示，因此使用 audio_only=True
                        voice.render_message(
                            last_msg.content,
                            container=st.session_state.last_message,
                            audio_only=True,
                        )
            else:
                response = await agent_client.ainvoke(
                    message=user_input,
                    model=model,
                    thread_id=st.session_state.thread_id,
                    user_id=user_id,
                )
                messages.append(response)
                # 显示模型回复，并按需附加语音
                with st.chat_message("ai"):
                    if voice and enable_audio:
                        voice.render_message(response.content)
                    else:
                        st.write(response.content)
            if is_first_message:
                fetch_user_threads_cached.clear()
            st.rerun()  # 清除过期容器
        except AgentClientError as e:
            st.error(f"Error generating response: {e}")
            st.stop()

    # 已经生成消息时，显示反馈控件
    if len(messages) > 0 and st.session_state.last_message:
        with st.session_state.last_message:
            await handle_feedback()


async def draw_messages(
    messages_agen: AsyncGenerator[ChatMessage | str, None],
    is_new: bool = False,
) -> None:
    """
    Draws a set of chat messages - either replaying existing messages
    or streaming new ones.

    This function has additional logic to handle streaming tokens and tool calls.
    - Use a placeholder container to render streaming tokens as they arrive.
    - Use a status container to render tool calls. Track the tool inputs and outputs
      and update the status container accordingly.

    The function also needs to track the last message container in session state
    since later messages can draw to the same container. This is also used for
    drawing the feedback widget in the latest chat message.

    Args:
        messages_aiter: An async iterator over messages to draw.
        is_new: Whether the messages are new or not.
    """

    # 记录最后一个消息容器
    last_message_type = None
    st.session_state.last_message = None

    # 用于显示生成中间文本片段的占位区域
    streaming_content = ""
    streaming_placeholder = None

    # 逐条遍历消息并绘制
    while msg := await anext(messages_agen, None):
        # 字符串类型的消息表示正在流式传输的中间文本片段
        if isinstance(msg, str):
            # 占位区域为空，表示这是新消息的第一个文本片段，
            # 因此需要先初始化显示区域
            if not streaming_placeholder:
                if last_message_type != "ai":
                    last_message_type = "ai"
                    st.session_state.last_message = st.chat_message("ai")
                with st.session_state.last_message:
                    streaming_placeholder = st.empty()

            streaming_content += msg
            streaming_placeholder.write(streaming_content)
            continue
        if not isinstance(msg, ChatMessage):
            st.error(f"Unexpected message type: {type(msg)}")
            st.write(msg)
            st.stop()

        match msg.type:
            # 用户消息最简单，直接展示
            case "human":
                last_message_type = "human"
                st.chat_message("human").write(msg.content)

            # 智能体消息的情况最复杂，因为需要同时处理
            # 流式文本片段和工具调用
            case "ai":
                # 如果是新消息，将其保存到会话状态
                if is_new:
                    st.session_state.messages.append(msg)

                # 上一条不是模型消息时，创建新的聊天消息容器
                if last_message_type != "ai":
                    last_message_type = "ai"
                    st.session_state.last_message = st.chat_message("ai")

                with st.session_state.last_message:
                    # 消息正文非空时，将其显示出来
                    # 重置流式显示变量，为下一条消息做准备
                    if msg.content:
                        if streaming_placeholder:
                            streaming_placeholder.write(msg.content)
                            streaming_content = ""
                            streaming_placeholder = None
                        else:
                            st.write(msg.content)

                    if msg.tool_calls:
                        # 为每个工具调用创建状态容器，并按工具调用编号
                        # 保存这些容器，确保结果能够对应到
                        # 正确的状态容器
                        call_results = {}
                        for tool_call in msg.tool_calls:
                            # 控制权移交和普通工具调用使用不同的标签
                            if "transfer_to" in tool_call["name"]:
                                label = f"""💼 Sub Agent: {tool_call["name"]}"""
                            else:
                                label = f"""🛠️ Tool Call: {tool_call["name"]}"""

                            status = st.status(
                                label,
                                state="running" if is_new else "complete",
                            )
                            call_results[tool_call["id"]] = status

                        # 每个工具调用预期对应一条 ToolMessage
                        for tool_call in msg.tool_calls:
                            if "transfer_to" in tool_call["name"]:
                                status = call_results[tool_call["id"]]
                                status.update(expanded=True)
                                await handle_sub_agent_msgs(messages_agen, status, is_new)
                                break

                            # 只有非移交类工具调用会进入这里
                            status = call_results[tool_call["id"]]
                            status.write("Input:")
                            status.write(tool_call["args"])
                            tool_result: ChatMessage = await anext(messages_agen)

                            if tool_result.type != "tool":
                                st.error(f"Unexpected ChatMessage type: {tool_result.type}")
                                st.write(tool_result)
                                st.stop()

                            # 如果是新消息则记录下来，再使用执行结果更新
                            # 对应的状态容器
                            if is_new:
                                st.session_state.messages.append(tool_result)
                            if tool_result.tool_call_id:
                                status = call_results[tool_result.tool_call_id]
                            status.write("Output:")
                            status.write(tool_result.content)
                            status.update(state="complete")

            case "custom":
                # 处理服务返回的自定义进度数据
                # 参见：
                # - src/agents/utils.py 中的 CustomData
                # - 文件 src/agents/bg_task_agent/task.py
                try:
                    task_data: TaskData = TaskData.model_validate(msg.custom_data)
                except ValidationError:
                    st.error("Unexpected CustomData message received from agent")
                    st.write(msg.custom_data)
                    st.stop()

                if is_new:
                    st.session_state.messages.append(msg)

                if last_message_type != "task":
                    last_message_type = "task"
                    st.session_state.last_message = st.chat_message(
                        name="task", avatar=":material/manufacturing:"
                    )
                    with st.session_state.last_message:
                        status = TaskDataStatus()

                status.add_and_draw_task_data(task_data)

            # 遇到非预期消息类型时，记录错误并停止
            case _:
                st.error(f"Unexpected ChatMessage type: {msg.type}")
                st.write(msg)
                st.stop()


async def handle_feedback() -> None:
    """Draws a feedback widget and records feedback from the user."""

    # 记录上次已发送的反馈，避免重复发送
    if "last_feedback" not in st.session_state:
        st.session_state.last_feedback = (None, None)

    latest_run_id = st.session_state.messages[-1].run_id
    feedback = st.feedback("stars", key=latest_run_id)

    # 反馈值或运行编号改变时，发送新的反馈记录
    if feedback is not None and (latest_run_id, feedback) != st.session_state.last_feedback:
        # 将反馈选项下标换算成 0 到 1 之间的分数
        normalized_score = (feedback + 1) / 5.0

        agent_client: AgentClient = st.session_state.agent_client
        try:
            await agent_client.acreate_feedback(
                run_id=latest_run_id,
                key="human-feedback-stars",
                score=normalized_score,
                kwargs={"comment": "In-line human feedback"},
            )
        except AgentClientError as e:
            st.error(f"Error recording feedback: {e}")
            st.stop()
        st.session_state.last_feedback = (latest_run_id, feedback)
        st.toast("Feedback recorded", icon=":material/reviews:")


async def handle_sub_agent_msgs(messages_agen, status, is_new):
    """
    This function segregates agent output into a status container.
    It handles all messages after the initial tool call message
    until it reaches the final AI message.

    Enhanced to support nested multi-agent hierarchies with handoff back messages.

    Args:
        messages_agen: Async generator of messages
        status: the status container for the current agent
        is_new: Whether messages are new or replayed
    """
    nested_popovers = {}

    # 寻找表示控制权移交成功的工具消息
    first_msg = await anext(messages_agen)
    if is_new:
        st.session_state.messages.append(first_msg)

    # 持续读取，直到明确收到控制权交还
    while True:
        # 读取下一条消息
        sub_msg = await anext(messages_agen)

        # 只有移除 skip_stream 标记后，才应出现这种情况
        # if isinstance(sub_msg, str):
        #     continue

        if is_new:
            st.session_state.messages.append(sub_msg)

        # 使用嵌套弹出区域展示工具结果
        if sub_msg.type == "tool" and sub_msg.tool_call_id in nested_popovers:
            popover = nested_popovers[sub_msg.tool_call_id]
            popover.write("**Output:**")
            popover.write(sub_msg.content)
            continue

        # 处理 transfer_back_to 工具调用，它表示子智能体正在交还控制权
        if (
            hasattr(sub_msg, "tool_calls")
            and sub_msg.tool_calls
            and any("transfer_back_to" in tc.get("name", "") for tc in sub_msg.tool_calls)
        ):
            # 处理交还控制权的 transfer_back_to 工具调用
            for tc in sub_msg.tool_calls:
                if "transfer_back_to" in tc.get("name", ""):
                    # 读取对应的工具执行结果
                    transfer_result = await anext(messages_agen)
                    if is_new:
                        st.session_state.messages.append(transfer_result)

            # 处理完控制权交还后，当前智能体的展示流程结束
            if status:
                status.update(state="complete")
            break

        # 在同一个嵌套状态容器中显示正文和工具调用
        if status:
            if sub_msg.content:
                status.write(sub_msg.content)

            if hasattr(sub_msg, "tool_calls") and sub_msg.tool_calls:
                for tc in sub_msg.tool_calls:
                    # 检查是否又发生了嵌套的控制权移交或任务委派
                    if "transfer_to" in tc["name"]:
                        # 为子智能体创建嵌套状态容器
                        nested_status = status.status(
                            f"""💼 Sub Agent: {tc["name"]}""",
                            state="running" if is_new else "complete",
                            expanded=True,
                        )

                        # 递归处理当前子智能体的下一级子智能体
                        await handle_sub_agent_msgs(messages_agen, nested_status, is_new)
                    else:
                        # 普通工具调用，创建弹出详情区域
                        popover = status.popover(f"{tc['name']}", icon="🛠️")
                        popover.write(f"**Tool:** {tc['name']}")
                        popover.write("**Input:**")
                        popover.write(tc["args"])
                        # 使用工具调用编号保存弹出区域的引用
                        nested_popovers[tc["id"]] = popover


if __name__ == "__main__":
    asyncio.run(main())
