import asyncio
from typing import cast
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import MessagesState
from langgraph.graph.state import CompiledStateGraph

load_dotenv()

from agents import DEFAULT_AGENT, get_agent, load_agent, close_agents  # noqa: E402

# 默认智能体使用 StateGraph.compile()，返回 CompiledStateGraph



async def main() -> None:
    inputs: MessagesState = {
        "messages": [HumanMessage("银行卡退款多久到账？")]
    }
    try:
        await load_agent(DEFAULT_AGENT)
        agent = cast(CompiledStateGraph, get_agent(DEFAULT_AGENT))
        result = await agent.ainvoke(
            input=inputs,
            config=RunnableConfig(configurable={"thread_id": str(uuid4())}),
        )
        result["messages"][-1].pretty_print()
    finally:
        await close_agents()




if __name__ == "__main__":
    asyncio.run(main())
