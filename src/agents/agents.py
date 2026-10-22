import os
from dataclasses import dataclass

from langgraph.graph.state import CompiledStateGraph
from langgraph.pregel import Pregel

from agents.lazy_agent import LazyLoadingAgent
from agents.supportpilot_agent import supportpilot_agent
from agents.mcp_supportpilot import MCPSupportPilotAgent
from schema import AgentInfo

DEFAULT_AGENT = "supportpilot"

# 用于兼容 LangGraph 不同智能体形式的类型别名
# - 使用 @entrypoint 定义的函数返回 Pregel
# - StateGraph().compile() 返回 CompiledStateGraph
AgentGraph = CompiledStateGraph | Pregel  # get_agent() 返回的类型（已完成加载）
AgentGraphLike = CompiledStateGraph | Pregel | LazyLoadingAgent  # 注册表中允许保存的类型


@dataclass
class Agent:
    description: str
    graph_like: AgentGraphLike


tool_backend = os.getenv("SUPPORTPILOT_TOOL_BACKEND", "mcp")
if tool_backend not in {"local", "mcp"}:
    raise ValueError("SUPPORTPILOT_TOOL_BACKEND必须为local或mcp。")

agents: dict[str, Agent] = {
    "supportpilot": Agent(
        description="CommerceDesk 电商客服：查询本地订单，检索商城政策并标注来源。",
        graph_like=MCPSupportPilotAgent() if tool_backend == "mcp" else supportpilot_agent,
    ),

}


async def load_agent(agent_id: str) -> None:
    """Load lazy agents if needed."""
    graph_like = agents[agent_id].graph_like
    if isinstance(graph_like, LazyLoadingAgent):
        await graph_like.load()


def get_agent(agent_id: str) -> AgentGraph:
    """Get an agent graph, loading lazy agents if needed."""
    agent_graph = agents[agent_id].graph_like

    # 如果是延迟加载智能体，先确认已加载，再返回其执行图
    if isinstance(agent_graph, LazyLoadingAgent):
        if not agent_graph._loaded:
            raise RuntimeError(f"Agent {agent_id} not loaded. Call load() first.")
        return agent_graph.get_graph()

    # 否则直接返回执行图
    return agent_graph


def get_all_agent_info() -> list[AgentInfo]:
    return [
        AgentInfo(key=agent_id, description=agent.description) for agent_id, agent in agents.items()
    ]


async def close_agents():
    """与load_agent在同一个生命周期任务中释放MCP进程与会话。"""
    for entry in reversed(list(agents.values())):
        if isinstance(entry.graph_like, MCPSupportPilotAgent):
            await entry.graph_like.aclose()
