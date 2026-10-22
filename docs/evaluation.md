# 评测与复现

| 题集 | 用途 |
| --- | --- |
| `rag_cases.jsonl`：66条 | 开发集22条、测试集44条；检索对比 |
| `query_rewrite_cases.jsonl`：30条 | 口语、补充、更正及无答案的端到端诊断 |
| `policy_refresh_holdout.jsonl`：20条 | 代码修改前编写的新题配对验证 |

题集位于 `data/evaluation/`。必要证据按片段ID标注：组间需共同覆盖，组内允许替代。

## 结果

检索测试集36条有答案题：原向量策略证据命中19条、混合检索35条、重排后36条。原向量策略使用距离阈值0.7；去掉旧阈值后可命中34条，不能把全部收益归因于混合检索。

证据检查改动的配对结果：

| 指标 | 原30题：旧版→新版 | 新20题：旧版→新版 |
| --- | --- | --- |
| 模型裁判判正确 | 27/30→28/30 | 16/20→19/20 |
| 完整必要证据 | 18/24→21/24 | 11/16→12/16 |
| 无答案明确拒答 | 6/6→6/6 | 4/4→4/4 |

旧题2条改善、1条退化；新题3条改善、无新增退化。新版增加84次检查模型调用，不能宣称降低成本。67条相关离线测试通过。

## 运行

先按运行说明配置模型和索引，设置 `PYTHONPATH=src`：

```powershell
.venv/Scripts/python.exe scripts/evaluate_rag.py --help
.venv/Scripts/python.exe scripts/evaluate_rag_answers.py --help
.venv/Scripts/python.exe scripts/evaluate_query_rewrite.py --help
.venv/Scripts/python.exe scripts/evaluate_policy_refresh.py --stage regression --output work/policy-refresh-regression.json
.venv/Scripts/python.exe scripts/evaluate_policy_refresh.py --stage holdout --output work/policy-refresh-holdout.json
```

在线脚本会发送测试对话及政策片段到配置的模型接口并产生用量；本地日志保存在work，不上传密钥或订单数据库。

## 数据与局限

原始检索对比：[rag-comparison.json](rag-comparison.json)；回答对比：[rag-answer-comparison.json](rag-answer-comparison.json)；查询轨迹：[query-rewrite-results.json](query-rewrite-results.json)；取证配对：[policy-refresh-results.json](policy-refresh-results.json)。

这是内部小样本，回答由同模型家族裁判，未做独立人工盲审。配对各运行一次，存在随机性；当前结果不代表真实业务准确率。新题首次运行因Chroma初始化竞争出现工具错误，记录保留在work；串行初始化后整批重跑，正式结果工具错误为0。

金卡、手机开箱和跨品类券的阶段诊断与补证回归见 [漏检修复报告](rag-miss-fix.md)。补充的三案例在线验证见 [在线验证记录](rag-miss-online-validation.md)：真实流程主要结论正确，固定单轮仍有过度拒答；这些是本助手逐条核对结果，不替换上述旧批次模型裁判指标。新增检查仍可能误判复用。评测保留失败和退化，不以命中率代替回答正确率。

## 回答质量与检查成本迭代

18题（3已知+15初轮新增）冻结旧源码对照、最终回归与auto/always成本控制见 [本轮报告](answer-quality-results.md)。最终回归已经参与提示调整，不能称新留出集。原模型裁判指标保持原批次，不与本助手逐条复核混算。

旧50题全部8条改善/退化/失败的复核见 [旧题清单](prior-evaluation-review.md)；新18题见 [逐案清单](answer-quality-review.md)。两者均由本助手复核，真人盲审仍待签认。

## 真实 MCP 与语义边界回归补充

最终18题、23轮真实模型与生产MCP回归、固定无证据补测和107项离线测试已完成，逐案结果、成本和中间失败见 [本轮报告](mcp-semantic-regression.md)。本文之前的批次指标仍是历史记录，不改写为新成绩。
