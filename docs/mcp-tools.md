# MCP 工具接入

项目将订单查询、政策检索封装为本地 stdio MCP Server，LangGraph 通过官方 `langchain-mcp-adapters` 发现并调用工具。催单与人工审批仍在本地业务图执行。

```text
LangGraph → MCP适配器 → 持久stdio会话 → MCP Server
                                      ├─ get_order_status → SQLite
                                      └─ search_knowledge_base → 政策检索
```

Server 和本地工具共用 `src/business/readonly_tools.py`，保持相同业务返回格式。接口复用的代价是子进程、预热时间和协议开销。

## 工具契约

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| get_order_status | order_id：1–64字符字符串 | 原订单 JSON 或明确业务错误；订单号经过规范化与格式校验 |
| search_knowledge_base | query：1–1200字符非空字符串 | found／no_evidence／error；保留片段 ID、来源、正文、上下文和分数 |

参数通过 Pydantic 校验。两项工具声明只读与幂等；实际只读范围由注册的读取函数决定。进程以当前用户权限运行。

适配层将 MCP 文本块还原为原字符串响应，并校验政策状态和 `matches`。传输、格式或关闭后调用失败返回明确错误；连接失败不会静默改走本地工具。模型绑定与 ToolNode 执行使用同一份工具列表。

## 生命周期与配置

FastAPI 启动时创建会话并发现工具，请求间复用，关闭时释放。会话进入与退出必须在同一异步生命周期任务中完成。API 注册表和 `src/run_agent.py` 默认使用 MCP。

```dotenv
SUPPORTPILOT_TOOL_BACKEND=mcp
SUPPORTPILOT_MCP_TIMEOUT=60
```

改为 `SUPPORTPILOT_TOOL_BACKEND=local` 并重启，可切回进程内工具。直接导出的 `supportpilot_agent` 和默认 `build_supportpilot_graph()` 保留本地工具，供原评测与 LangGraph CLI 使用。

子进程仅接收订单、索引、检索和模型缓存相关配置，不转发模型接口密钥；默认离线读取检索模型。stdout 用于协议，诊断写 stderr。独立启动使用项目虚拟环境 Python，参数 `-m tool_protocol.server`，工作目录为项目根，`PYTHONPATH` 指向 `src`。

Windows 实测中，首次工具调用从工作线程导入 NumPy 曾发生阻塞。当前在协议启动前由主线程加载数值依赖和缓存模型，检索再交给工作线程。启动耗时因此包含预热，需先按[运行说明](../LOCAL_SUPPORTPILOT.md)准备模型缓存。

## 验证

```powershell
.venv/Scripts/python.exe scripts/verify_mcp_tools.py
$env:PYTHONPATH='src;tests;tests/agents'
$env:USE_FAKE_MODEL='true'
.venv/Scripts/python.exe -m unittest test_mcp_integration -v
```

独立验证使用原生 SDK 客户端、生产 Server、临时演示订单和同源索引副本，检查发现工具、参数拒绝、订单一致性、政策正文与上下文、索引缺失错误。客户端不加载 Agent 或 Torch。

| 三次顺序热调用的中位数 | 本地 | MCP |
| --- | ---: | ---: |
| 订单查询 | 0.68 ms | 2.92 ms |
| 政策检索 | 880.00 ms | 952.70 ms |

该批启动、预热与工具发现共12.06秒，预热后首次政策调用约1秒；本地首次调用包含延迟初始化，不能直接比较冷调用。数据为本机小样本。

11项 MCP 集成测试覆盖参数、长中文响应、并发调用、关闭与启动失败、证据复用、条件更正、本地催单边界及 FastAPI 生命周期。另已完成[真实模型与 MCP 回归](mcp-semantic-regression.md)。当前仅提供本地 stdio 接入，远程 HTTP MCP、认证和租户隔离不在实现范围内。
