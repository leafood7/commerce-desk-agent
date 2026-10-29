> 历史兼容资料：主要描述原Agent演示。当前新工作台以[作品交付包](portfolio/README.md)、[实际架构](portfolio/architecture.md)和[三段演示](portfolio/demos.md)为准。

# 架构与调用链

```text
Streamlit → AgentClient → FastAPI → LangGraph
                                    ├─ MCP 客户端 → stdio Server → 只读订单／政策
                                    └─ 本地催单／审批 → SQLite
```

## 聊天与政策

```text
用户消息
→ auto完整匹配独立问候／致谢／纯查单：省检查，交给model正常回复或查单
→ 其余进入check_policy_evidence：判断当前问题及证据是否足够
→ 不足：ToolNode → search_knowledge_base → search_policies
→ 足够：复用指定的真实工具片段
→ call_model：回答或继续选择工具
→ 有工具请求则 tools → model，否则结束
```

快捷路径只省检查模型调用，不跳过订单工具；混合问题、更正和复述仍核对证据。`SUPPORTPILOT_POLICY_CHECK_MODE=always`可恢复每轮检查。

API 默认经 MCP 调用 `get_order_status → order_result → get_order_from_db`；政策工具同样经 MCP 复用 `policy_result → search_policies`。本地后端可通过配置回退，详细契约与验证见 [MCP 文档](mcp-tools.md)。

订单查询缺少订单号时询问；一般政策说明无需订单号。

## 催单与审批

```text
request_shipping → submit_shipping → submit_request
→ 普通催单：校验后登记工单
→ 加急：run_approval → review → interrupt，等待人工

网页审批 → shipping_api → run_approval → Command(resume=...)
→ execute_decision：复查订单 → 事务内检查申请 → 写工单与审批结果
```

普通催单要求已付款且待发货；加急还要求超过订单承诺时间。批准前重新检查，条件变化则使申请失效；拒绝不创建工单。事务和唯一约束防止重复批准产生重复工单。

会话检查点、审批检查点和订单业务表分别保存。客户聊天中的“批准”不能替代审批接口。

## 核心文件

| 文件 | 作用 |
| --- | --- |
| `src/agents/supportpilot_agent.py` | 图、证据检查节点、模型与工具 |
| `src/agents/policy_evidence.py` | 真实证据提取、检查结果校验及窄范围快捷分流 |
| `src/agents/answer_policy.py` | 同一次生成共享的规则应用与工具边界约束 |
| `src/business/knowledge.py` | 政策切块、索引与检索入口 |
| `src/business/rag_retrieval.py` | BM25、融合与重排 |
| `src/business/shipping.py` | 催单校验、审批图和事务执行 |
| `src/business/order_repository.py` | 订单与工单表 |
| `src/service/shipping_api.py` | 审批接口 |
| `src/shipping_ui.py` | 网页审批操作 |

只读工具进程实现位于 `src/tool_protocol/`；`src/agents/mcp_supportpilot.py` 管理会话与图的生命周期，`src/business/readonly_tools.py` 保存两种后端共用的业务响应。
