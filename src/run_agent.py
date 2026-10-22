import asyncio
from typing import cast
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import MessagesState
from langgraph.graph.state import CompiledStateGraph

load_dotenv()

from agents import DEFAULT_AGENT, get_agent  # noqa: E402

# 默认智能体使用 StateGraph.compile()，返回 CompiledStateGraph
agent = cast(CompiledStateGraph, get_agent(DEFAULT_AGENT))


async def main() -> None:
    inputs: MessagesState = {
        "messages": [HumanMessage("Find me a recipe for chocolate chip cookies")]
    }
    result = await agent.ainvoke(
        input=inputs,
        config=RunnableConfig(configurable={"thread_id": uuid4()}),
    )
    result["messages"][-1].pretty_print()

    # 将智能体执行图绘制成 PNG 图片
    # 需要先安装以下依赖：
    # 安装 Graphviz：brew install graphviz
    # 设置编译头文件路径：export CFLAGS="-I $(brew --prefix graphviz)/include"
    # 设置编译库路径：export LDFLAGS="-L $(brew --prefix graphviz)/lib"
    # 安装绘图依赖：pip install pygraphviz
    #
    # 绘图示例：agent.get_graph().draw_png("agent_diagram.png")


if __name__ == "__main__":
    asyncio.run(main())
