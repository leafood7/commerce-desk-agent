from dataclasses import dataclass

from langgraph.graph.state import CompiledStateGraph
from langgraph.pregel import Pregel

from agents.bg_task_agent.bg_task_agent import bg_task_agent
from agents.chatbot import chatbot
from agents.command_agent import command_agent
from agents.github_mcp_agent.github_mcp_agent import github_mcp_agent
from agents.interrupt_agent import interrupt_agent
from agents.knowledge_base_agent import kb_agent
from agents.langgraph_supervisor_agent import langgraph_supervisor_agent
from agents.langgraph_supervisor_hierarchy_agent import langgraph_supervisor_hierarchy_agent
from agents.lazy_agent import LazyLoadingAgent
from agents.rag_assistant import rag_assistant
from agents.research_assistant import research_assistant
from agents.supportpilot_agent import supportpilot_agent
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


agents: dict[str, Agent] = {
    "supportpilot": Agent(
        description="CommerceDesk 电商客服：查询本地订单，检索商城政策并标注来源。",
        graph_like=supportpilot_agent,
    ),
    "chatbot": Agent(description="A simple chatbot.", graph_like=chatbot),
    "research-assistant": Agent(
        description="A research assistant with web search and calculator.",
        graph_like=research_assistant,
    ),
    "rag-assistant": Agent(
        description="A RAG assistant with access to information in a database.",
        graph_like=rag_assistant,
    ),
    "command-agent": Agent(description="A command agent.", graph_like=command_agent),
    "bg-task-agent": Agent(description="A background task agent.", graph_like=bg_task_agent),
    "langgraph-supervisor-agent": Agent(
        description="A langgraph supervisor agent", graph_like=langgraph_supervisor_agent
    ),
    "langgraph-supervisor-hierarchy-agent": Agent(
        description="A langgraph supervisor agent with a nested hierarchy of agents",
        graph_like=langgraph_supervisor_hierarchy_agent,
    ),
    "interrupt-agent": Agent(
        description="An agent the uses interrupts.", graph_like=interrupt_agent
    ),
    "knowledge-base-agent": Agent(
        description="A retrieval-augmented generation agent using Amazon Bedrock Knowledge Base",
        graph_like=kb_agent,
    ),
    "github-mcp-agent": Agent(
        description="A GitHub agent with MCP tools for repository management and development workflows.",
        graph_like=github_mcp_agent,
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
