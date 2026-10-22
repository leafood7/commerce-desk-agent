from langchain.agents import create_agent
from langgraph_supervisor import create_supervisor

from agents.langgraph_supervisor_agent import add, multiply, web_search
from core import get_model, settings

model = get_model(settings.DEFAULT_MODEL)


def workflow(chosen_model):
    math_agent = create_agent(
        model=chosen_model,
        tools=[add, multiply],
        name="sub-agent-math_expert",  # 将该图节点标记为子智能体
        system_prompt="You are a math expert. Always use one tool at a time.",
    ).with_config(tags=["skip_stream"])

    research_agent = (
        create_supervisor(
            [math_agent],
            model=chosen_model,
            tools=[web_search],
            prompt="You are a world class researcher with access to web search. Do not do any math, you have a math expert for that. ",
            supervisor_name="supervisor-research_expert",  # 将该图节点标记为数学智能体的监督者
        )
        .compile(
            name="sub-agent-research_expert"
        )  # 将该图节点标记为主监督者的子智能体
        .with_config(tags=["skip_stream"])
    )  # 网页会忽略子智能体逐个输出的文本片段

    # 创建监督者工作流
    return create_supervisor(
        [research_agent],
        model=chosen_model,
        prompt=(
            "You are a team supervisor managing a research expert with math capabilities."
            "For current events, use research_agent. "
        ),
        add_handoff_back_messages=True,
        # 网页现在要求此值为 True，以便明确识别控制权交还，而不必猜测
        output_mode="full_history",  # 否则重新加载对话时不会包含子智能体的消息
    )  # 监督者的默认名称是 "supervisor"


langgraph_supervisor_hierarchy_agent = workflow(model).compile()
