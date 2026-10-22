# MCP 与语义边界迭代记录

运行日期：2026-10-22；最终代码版本：`e1067a8`。当前说明见[真实 MCP 回归](../mcp-semantic-regression.md)。各批输出分别保留，不用后续结果覆盖早期失败。

| 批次 | 范围 | 观察结果 | 原始数据 |
| --- | --- | --- | --- |
| before | 2题 | 明确品类限制仍被写成假设；未知活动名本次已检索到条款并正确回答 | [JSON](../mcp-semantic-before.json) |
| before-controlled | 1题，固定历史查询 | 实际空召回，修复前本次也正确拒答 | [JSON](../mcp-semantic-before-controlled.json) |
| after | 18题、23轮 | “食品券”简称仍可能被当成确定限制 | [JSON](../mcp-semantic-after.json) |
| final | 18题、23轮 | 金卡第2轮有效复用因空查询被拒绝，发生检查回退，且漏记该调用 usage | [JSON](../mcp-semantic-final.json) |
| final-v2 | 18题、23轮 | 修复复用校验及用量统计后，轨迹检查通过，无工具错误与检查回退 | [JSON](../mcp-semantic-final-v2.json) |
| final-controlled | 1题，固定历史查询 | 真实空结果，回答未补写无证据政策 | [JSON](../mcp-semantic-final-controlled.json) |

初版评测脚本还因 SQLite 连接未显式关闭而无法清理临时目录，两题模型结果完整保留；连接释放已修复。中间回退批次有一次缺失 usage，已记录用量不能视为开发过程的完整账单。

## 可复现性

冻结源码位于 `data/evaluation/baselines/mcp_semantics/`，manifest 与 before 记录中的哈希一致。冻结文件和题集通过 `.gitattributes` 保留原始字节，避免跨平台换行转换改变哈希。

18题参与过多轮提示调整；本阶段未增加独立留出集，复核采用 AI 辅助方式。固定空查询通过覆盖检查器查询控制检索输入，与自由查询实验分别报告。
