"""Thread enumeration for the /threads endpoints.

Threads are derived from the checkpointer rather than a table of their own, so listing
them means reading checkpoint metadata the way LangGraph writes it. The invariants that
makes possible are documented at the constants below.
"""

import logging
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from schema import ThreadSummary
from service.utils import convert_message_content_to_string, messages_from_checkpoint

logger = logging.getLogger(__name__)

# LangGraph 每个对话只在第 -1 步写入一次输入检查点，后续轮次从
# 上次步骤继续；不要换成其他固定步骤，因为单轮对话从未到达第 1 步
THREAD_HEAD_STEP = -1

# 起始检查点按对话创建时间排序，因此多取一些记录，再按最新检查点排序，
# 近似得到“最近更新”的对话；早于本次最旧起始记录的对话不会进入结果
MAX_THREAD_HEADS = 200
HEAD_PAGE_SIZE = 200

# 一条起始记录不一定对应独立对话：子图每次调用也会写入一条，并继承父级元数据；
# 因此需要限制为补偿这种情况而进行的分页次数
MAX_HEAD_ROWS = 1000

TITLE_MAX_LENGTH = 60


async def _list_thread_heads(checkpointer: Any, user_id: str, agent_id: str) -> list[Any]:
    """Return one head checkpoint per thread, newest thread first."""
    heads: list[Any] = []
    seen: set[str] = set()
    rows_scanned = 0
    before = None
    while len(seen) < MAX_THREAD_HEADS and rows_scanned < MAX_HEAD_ROWS:
        page = [
            c
            async for c in checkpointer.alist(
                None,
                filter={"user_id": user_id, "agent_id": agent_id, "step": THREAD_HEAD_STEP},
                before=before,
                limit=HEAD_PAGE_SIZE,
            )
        ]
        if not page:
            break
        rows_scanned += len(page)
        short_page = len(page) < HEAD_PAGE_SIZE
        for row in page:
            thread_id = row.config["configurable"]["thread_id"]
            if thread_id in seen:
                continue
            seen.add(thread_id)
            heads.append(row)
        if short_page:
            break
        before = RunnableConfig(
            configurable={"checkpoint_id": page[-1].config["configurable"]["checkpoint_id"]}
        )
    return heads


async def list_user_threads(
    checkpointer: Any, user_id: str, agent_id: str, limit: int
) -> list[ThreadSummary]:
    """List a user's threads for an agent, most recently updated first."""
    summaries: list[tuple[str, ThreadSummary]] = []
    for head in await _list_thread_heads(checkpointer, user_id, agent_id):
        thread_id = head.config["configurable"]["thread_id"]
        stored_user_id = head.metadata.get("user_id")
        stored_agent_id = head.metadata.get("agent_id")
        if stored_user_id != user_id or stored_agent_id != agent_id:
            logger.warning(
                f"Checkpointer returned thread {thread_id} with user_id "
                f"{stored_user_id!r}/agent_id {stored_agent_id!r}, expected "
                f"{user_id!r}/{agent_id!r} — skipping to avoid a "
                "cross-user or cross-agent leak."
            )
            continue

        # 起始检查点还没有消息，标题和更新时间应从最新检查点取得
        tip = await checkpointer.aget_tuple(RunnableConfig(configurable={"thread_id": thread_id}))
        if tip is None:
            continue
        messages = messages_from_checkpoint(tip.checkpoint)
        first_human = next((m for m in messages if isinstance(m, HumanMessage)), None)
        summaries.append(
            (
                tip.config["configurable"]["checkpoint_id"],
                ThreadSummary(
                    thread_id=thread_id,
                    agent_id=agent_id,
                    updated_at=tip.checkpoint.get("ts"),
                    title=convert_message_content_to_string(first_human.content)[:TITLE_MAX_LENGTH]
                    if first_human
                    else None,
                ),
            )
        )

    # 检查点编号是按时间排序的 UUID，因此可按最新检查点编号排列更新时间
    summaries.sort(key=lambda item: item[0], reverse=True)
    return [summary for _, summary in summaries[:limit]]
