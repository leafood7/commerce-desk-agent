# 文档导航

首次运行从[配置与运行](../LOCAL_SUPPORTPILOT.md)开始；了解实现看[架构](architecture.md)或[架构摘要](architecture-summary.md)，核对效果看[评测总览](evaluation.md)。

## 当前说明

| 文档 | 用途 |
| --- | --- |
| [架构与调用链](architecture.md) | 对话图、MCP 工具与人工审批 |
| [架构摘要](architecture-summary.md) | 调用链、数据层、配置边界与开发清单 |
| [RAG 检索](rag-optimization.md) | 模型、检索参数与同源条款补全 |
| [证据检查](policy-evidence-refresh.md) | 重新检索、复用与失败回退 |
| [MCP 工具](mcp-tools.md) | 工具契约、启动与独立验证 |
| [补证实验与语义修复](policy-semantics.md) | 对照收益、语义失败、成本及回归入口 |
| [评测总览](evaluation.md) | 各批次结果与复现入口 |
| [真实 MCP 回归](mcp-semantic-regression.md) | 最新23轮的结果、成本和逐案记录 |
| [演示流程](demo-and-interview.md) | 三条业务流程与技术说明 |
| [知识库来源](knowledge-sources.md) | 演示规则的出处及适配说明 |

## 历史与数据

[历史记录](history/README.md)按阶段整理旧报告。旧文件入口保留跳转说明；报告中的测试数、失败状态和指标对应各自批次。

原始 JSON 结果仍保存在本目录，冻结题集和源码基线位于 `data/evaluation/`。本次文档整理不修改这些数据。[AG-UI 协议说明](AGUI.md)作为接口参考保留。
