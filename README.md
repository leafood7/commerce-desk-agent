# CommerceDesk · 商询

面向电商客服场景的 Agent 工作台，基于 LangGraph、FastAPI 和 Streamlit，支持政策问答、订单查询、催单与人工审批。

## 主要功能

- 政策问答：BGE 向量与 BM25 混合检索，经 RRF 融合、重排和同源条款补全后返回证据与来源。
- 多轮对话：根据最新条件核对证据，支持用户更正和真实片段复用。
- 订单工具：通过本地 stdio MCP Server 提供订单查询与政策检索，Agent 复用持久连接。
- 催单审批：普通催单按业务条件登记；加急申请暂停等待人工批准，执行前复查订单，并通过事务与唯一约束防止重复建单。

```text
Streamlit → FastAPI → LangGraph
                      ├─ MCP 客户端 → 只读订单／政策工具
                      └─ 本地催单／人工审批 → SQLite
```

## 运行

需要 Python 3.12–3.14 和 uv。

```powershell
uv sync --frozen --no-dev --extra supportpilot
Copy-Item .env.example .env
# 在 .env 填写模型接口配置
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

网页：<http://127.0.0.1:8501>；API：<http://127.0.0.1:8081/docs>。首次运行需下载检索模型，完整配置见[运行说明](LOCAL_SUPPORTPILOT.md)。

## 验证结果

最新一批真实模型与 MCP 回归包含18条合成对话、23轮消息，完成15次政策检索和3次订单查询，未出现工具错误、检查回退或意外业务写入。107项离线测试在待提交内容的独立快照中通过。

这些题目参与过调试，用于回归验证；语义结果采用 AI 辅助复核，尚未独立人工盲审。详细方法、成本和失败记录见[评测总览](docs/evaluation.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [配置与运行](LOCAL_SUPPORTPILOT.md) | 环境、模型缓存、启动、后端切换 |
| [架构](docs/architecture.md) | 对话、工具与审批调用链 |
| [架构摘要](docs/architecture-summary.md) | 调用链、数据层与下一步开发清单 |
| [RAG 检索](docs/rag-optimization.md) | 召回、重排、补证与参数 |
| [MCP 接入](docs/mcp-tools.md) | 工具契约、会话管理、独立客户端 |
| [评测总览](docs/evaluation.md) | 最新结果、指标口径与复现命令 |
| [演示流程](docs/demo-and-interview.md) | 政策问答、条件更正、加急审批 |
| [知识库来源](docs/knowledge-sources.md) | 演示政策的来源与适配范围 |
| [历史记录](docs/history/README.md) | 各阶段诊断、成本对照和争议案例 |

## 使用范围与来源

项目适用于本地演示和工程实践。订单与政策为演示数据，未接入真实仓库、退款或开票系统；当前没有多租户隔离，也未进行生产容量验证。

基于 [JoshuaC215/agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit) 二次开发，沿用服务、会话与模型接入基础，新增电商业务、政策检索、证据检查、MCP 接入及评测。版权与修改说明见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE.md)。
