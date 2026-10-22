# 只读 MCP 工具与 LangGraph 接入

## 实现范围

将现有订单查询、政策检索暴露为本地 stdio MCP Server；CommerceDesk Agent 同时作为 MCP 客户端使用它们。LangGraph 继续负责状态、证据检查、工具选择与审批流程。这两个方向在本项目中同时存在：Server 提供工具，Agent 经客户端消费工具。

```text
Streamlit → FastAPI → LangGraph
                      ├─ 官方 MCP 适配器 → 持久 stdio 会话 → MCP Server
                      │                                  ├─ get_order_status → SQLite
                      │                                  └─ search_knowledge_base → Chroma/BM25/RRF/重排
                      └─ request_shipping → 本地审批图 → 复查/事务/去重
```

只暴露两项只读工具；催单和审批继续本地执行。没有增加端口、Redis、消息队列或微服务部署。工具进程和 Agent 共用原业务函数，避免维护两套查询逻辑。

## 启动与回退

按 LOCAL_SUPPORTPILOT.md 启动服务即可。API 注册表和 src/run_agent.py 默认使用 MCP；FastAPI 启动时创建一个会话并发现工具，多个请求复用，关闭时释放。客户端的进入、退出必须在同一异步生命周期任务中完成。

```dotenv
SUPPORTPILOT_TOOL_BACKEND=mcp
SUPPORTPILOT_MCP_TIMEOUT=60
```

改为 `SUPPORTPILOT_TOOL_BACKEND=local` 并重启可切回原进程内工具。连接失败会报告错误，不静默改走本地工具；政策检索错误不能当作找到证据。模型绑定和 ToolNode 使用同一份工具列表。

直接导出的 `supportpilot_agent` 图及默认 `build_supportpilot_graph()` 保留本地工具，供原评测、单元测试和 LangGraph CLI 使用；因此旧质量评测不能作为 MCP 路径已经完成真实模型端到端评测的证据。

Server 启动前在主线程预加载数值依赖和已有缓存模型，耗时计入启动。Windows 实测中，首次工具调用才从工作线程导入 NumPy 会卡住；主线程预加载后真实检索恢复。复制隔离索引本身未能解决此问题，不能归因为已证明的 Chroma 锁冲突。协议建立后同步检索交给工作线程，避免阻塞事件循环。

## 工具契约

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| get_order_status | order_id：1–64字符字符串 | 保持原订单 JSON 或原业务错误说明；订单号仍规范化校验 |
| search_knowledge_base | query：1–1200字符非空字符串 | 原 JSON：found / no_evidence / error，保留片段 ID、来源、正文、上下文与分数 |

两项工具声明 readOnlyHint、idempotentHint，参数经 Pydantic 验证。注解只是协议提示，真正的只读边界来自仅注册读取函数；stdio 进程仍以当前用户权限运行，不是文件系统沙箱。

官方 langchain-mcp-adapters 发现工具后，薄适配层将文本块还原为原字符串契约；政策证据检查继续读取真实片段 ID。传输、格式或关闭后调用失败返回明确 error，不伪造 no_evidence。每次工具调用不会新增一次大模型请求。

仅向子进程传递数据库、索引、检索和本地模型缓存相关配置，不转发模型 API 密钥。默认离线读取模型，首次安装应先用原初始化流程准备缓存。独立启动时命令为项目虚拟环境的 python，参数 `-m tool_protocol.server`，工作目录为项目根，环境 `PYTHONPATH` 为项目 src 目录。stdout 专用于 MCP 协议，诊断写 stderr。

## 独立验证与回归

```powershell
.venv/Scripts/python.exe scripts/verify_mcp_tools.py
$env:PYTHONPATH='src;tests;tests/agents'
$env:USE_FAKE_MODEL='true'
.venv/Scripts/python.exe -m unittest test_mcp_integration -v
```

独立验证使用原生 MCP SDK 客户端，不导入 Agent 或 Torch；通过真实 stdio 发现并调用生产 Server。虚构订单使用临时数据库，真实缓存政策使用同源隔离索引副本，本地基线进程退出后再启动 Server。不修改现有业务数据，不调用外部 LLM。

2026-10-22 本机验证结果见 `work/mcp-independent-validation.json`：工具清单、只读标注、订单一致性、参数拒绝、政策 ID/来源/正文/上下文一致性、金卡必要条款及索引缺失错误全部通过。跨进程模型分数不要求浮点逐位相同；协议夹具测试另行验证分数字段原样保留。

| 测量 | 本地 | MCP |
| --- | --- | --- |
| 查单热调用中位数（3次） | 0.68 ms | 2.92 ms |
| 政策热调用中位数（3次） | 880.00 ms | 952.70 ms |

MCP 启动、预热与发现合计约12.06秒；预热后首次政策调用约1秒。本地首次政策调用约14.43秒，包含延迟初始化，与 MCP 首次工具调用口径不同，不能据此宣称加速。以上是少量顺序样本，不是并发压测或 SLA。

本次按 CI 模块列表运行100项测试全部通过，其中新增 MCP 测试11项；CI 已加入这些测试，但尚未在远程 GitHub Actions 执行。

协议与集成测试覆盖工具发现、非法参数、未知订单、大中文响应、并发调用、断连/关闭错误、证据复用与条件更正、工具绑定、远程查单与本地催单、加载释放以及 FastAPI 生命周期。使用合成工具响应和假模型测试 Agent，不宣称完成了外部真实模型质量复测或网页点击验收。

## 项目与面试表述

可写：将订单查询与政策检索封装为只读 MCP Server，基于官方适配器接入 LangGraph；实现持久会话管理、参数校验、错误契约与本地后端回退配置，通过独立 SDK 客户端及图级回归验证工具复用和原有审批边界。

AI 辅助开发的价值体现在：自己确定只读边界、响应契约与验收标准，用 AI 辅助实现；根据真实进程的线程栈定位冷启动阻塞，验证后修正初始化方式；对照协议、真实结果和回归用例审查产出。代码由 AI 辅助生成不等于完成设计和验证，面试需能解释这些取舍。

当前只支持本地 stdio，未实现远程 HTTP MCP 的认证、多租户隔离或高并发容量验证。本次改造没有修复历史评测中仍存在的语义失败案例。

## 真实 MCP 与语义边界回归补充

最终18题、23轮真实模型与生产MCP回归、固定无证据补测和107项离线测试已完成，逐案结果、成本和中间失败见 [本轮报告](mcp-semantic-regression.md)。本文之前的批次指标仍是历史记录，不改写为新成绩。
