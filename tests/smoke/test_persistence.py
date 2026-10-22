import os

import pytest

from client import AgentClient

# 通过环境变量与 scripts/smoke_test.sh 共享，以便脚本确认
# 这个对话的检查点确实写入了预期存储后端；单独运行测试时
# 则回退使用固定编号
THREAD_ID = os.environ.get("SMOKE_THREAD_ID", "smoke-test-persistence-thread")


@pytest.mark.docker
def test_checkpointer_persists_history():
    """Confirm the configured checkpointer persists conversation state across turns.

    Backend-agnostic: exercises whichever DATABASE_TYPE the service was started
    with. scripts/smoke_test.sh runs this against both postgres and mongo, then
    separately verifies the data actually landed in that backend (this test alone
    can't tell the backends apart, since any working checkpointer would pass).
    Requires a running service (USE_FAKE_MODEL=true) backed by a live database.

    Uses the default agent for both invoke and get_history. Since /history is
    agent-aware and AgentClient scopes it to the client's selected agent, the same
    graph that created the thread also reads it back — which is what makes the
    round-trip work now that each agent is a distinct graph with its own state.
    """
    client = AgentClient("http://localhost:8080")

    client.invoke("Tell me a joke?", thread_id=THREAD_ID, model="fake")
    client.invoke("Tell me another?", thread_id=THREAD_ID, model="fake")

    history = client.get_history(thread_id=THREAD_ID)
    human_messages = [m for m in history.messages if m.type == "human"]
    assert len(human_messages) == 2
    assert human_messages[0].content == "Tell me a joke?"
    assert human_messages[1].content == "Tell me another?"


@pytest.mark.docker
def test_threads_lists_user_threads():
    """Confirm /threads enumerates threads through the configured checkpointer.

    The unit tests use a fake checkpointer and the SQLite integration tests only
    prove the SQLite driver, so this is the check that the metadata filter
    (including the step -1 head lookup) behaves the same on Postgres and Mongo.
    Requires a running service (USE_FAKE_MODEL=true) backed by a live database.
    """
    client = AgentClient("http://localhost:8080")
    user_id = f"{THREAD_ID}-user"
    other_user_id = f"{THREAD_ID}-other"

    single_turn = f"{THREAD_ID}-single"
    multi_turn = f"{THREAD_ID}-multi"
    client.invoke("Only turn", thread_id=single_turn, user_id=user_id, model="fake")
    client.invoke("First turn", thread_id=multi_turn, user_id=user_id, model="fake")
    client.invoke("Second turn", thread_id=multi_turn, user_id=user_id, model="fake")
    client.invoke("Not mine", thread_id=f"{THREAD_ID}-other", user_id=other_user_id, model="fake")

    threads = client.get_user_threads(user_id=user_id).threads
    # 最近更新的对话排在前面；即使单轮对话没有推进到起始检查点之后，
    # 也必须出现在列表中
    assert [t.thread_id for t in threads] == [multi_turn, single_turn]
    assert [t.title for t in threads] == ["First turn", "Only turn"]
    assert all(t.updated_at for t in threads)

    other_threads = client.get_user_threads(user_id=other_user_id).threads
    assert [t.thread_id for t in other_threads] == [f"{THREAD_ID}-other"]
