# 评测总览

当前最新结果来自18条合成对话、23轮真实模型与生产 MCP 回归。检索覆盖、回答质量、工具执行和调用成本分别统计，避免用单个分数概括整个 Agent。

## 最新结果

| 项目 | 结果 |
| --- | --- |
| 运行路径 | 真实模型 → LangGraph → 本地 stdio MCP → 订单／政策工具 → 回答 |
| 对话规模 | 18题，23轮 |
| 工具调用 | 15次政策检索，3次订单查询 |
| 工具错误／检查回退／意外业务写入 | 0／0／0 |
| 模型调用 | 检查19次，回答及工具选择26次 |
| API 报告用量 | 输入82,442，输出4,068，共86,510 tokens |
| 耗时 | 对话合计106.86秒，单轮中位数4.68秒；启动预热另计10.56秒 |
| 离线验证 | 107项测试通过，另有待提交快照验证 |

逐题复核未发现重大结论错误，手机开箱题仍有多余查单邀请。固定历史空召回查询的补测实际返回 `no_evidence`，回答没有补写未经支持的条件政策。完整过程见[真实 MCP 回归](mcp-semantic-regression.md)，原始结果见[mcp-semantic-final-v2.json](mcp-semantic-final-v2.json)。

这里的真实模型回归不包含 HTTP API、浏览器点击或并发压测。题集已用于提示调整，复核采用 AI 辅助方式，未经独立人工盲审；结果不能估计线上业务准确率。tokens 为接口报告用量，耗时为单次顺序运行数据。

## 题集与指标

| 题集 | 数量 | 用途与当前状态 |
| --- | ---: | --- |
| `rag_cases.jsonl` | 66 | 原检索开发集22题、测试集44题；后续也用于回归 |
| `query_rewrite_cases.jsonl` | 30 | 口语、补充、更正和无答案诊断 |
| `policy_refresh_holdout.jsonl` | 20 | 首次用于新题配对，现已成为已知回归集 |
| `answer_quality_cases.jsonl` | 18 | 条件应用、工具边界、问候和复述；已参与调试 |

文件位于 `data/evaluation/`。必要证据按片段 ID 标注：`evidence_groups` 组间需要共同覆盖，组内允许等价替代。

- **必要证据覆盖**：回答所需的条款是否出现在返回证据中。
- **回答质量**：是否回答最新问题，结论是否有依据，有没有错误承诺。
- **执行正确性**：工具、参数、返回结果与实际状态是否一致。
- **调用成本**：模型及工具次数、报告用量、缺失用量和耗时。

轨迹和引用来源存在性由程序检查，语义是否得到支持需逐题复核。检索到相关资料并不保证回答正确，安全拒答也不一定解决了用户问题。

## 历史对照

| 阶段 | 观察结果 | 详细记录 |
| --- | --- | --- |
| 检索策略 | 36条有答案测试题，原向量／混合／重排策略命中19／35／36；向量去掉旧阈值后为34 | [检索对比](rag-comparison.json) |
| 证据检查 | 旧30题模型裁判27→28，新20题16→19；旧题有1条退化 | [原始结果](policy-refresh-results.json)、[争议复核](history/2026-10-22-prior-evaluation-review.md) |
| 同源补证 | 三个固定查询必要证据补齐；116题回归未见覆盖下降，上下文变长 | [漏检诊断](history/2026-10-22-rag-miss-fix.md) |
| 检查快捷路径 | 同提示的7轮子集，逻辑模型调用15→11，tokens少约13.6%；耗时未改善 | [成本对照](history/2026-10-22-answer-quality.md) |
| MCP 与语义边界 | 最新18题、23轮无检查回退；固定空结果补测正确拒答 | [最新回归](mcp-semantic-regression.md) |

这些实验的题集、版本和指标不同，不合并成一个准确率。旧模型裁判漏掉过工具能力承诺，保留原分数并单独记录复核意见。

## 复现

在项目根目录配置环境及模型缓存后运行。离线测试不调用外部聊天模型：

```powershell
$env:PYTHONPATH='src;tests;tests/agents'
$env:USE_FAKE_MODEL='true'
$env:HF_HUB_OFFLINE='1'
.venv/Scripts/python.exe -m unittest test_policy_evidence test_policy_refresh_evaluation test_query_rewrite_evaluation test_rag_evaluation test_rag_retrieval test_knowledge test_order_repository test_shipping test_supportpilot_agent test_policy_tool test_policy_context test_policy_fastpath test_answer_quality_evaluation test_mcp_integration test_mcp_semantic_evaluation
```

独立 MCP 客户端验证使用缓存检索模型和临时演示库，不调用聊天模型：

```powershell
.venv/Scripts/python.exe scripts/verify_mcp_tools.py
```

真实模型回归会将合成对话、演示政策和虚构订单发送到 `.env` 配置的接口并产生用量。已有输出不覆盖：

```powershell
.venv/Scripts/python.exe scripts/evaluate_mcp_semantics.py --online --output work/my-mcp-regression.json
```

旧实验的运行命令、源码基线与数据链接见[历史记录](history/README.md)。
