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

仍有金卡、手机开箱和跨品类券等漏检；新增检查也可能误判复用。评测保留失败和退化，不以命中率代替回答正确率。
