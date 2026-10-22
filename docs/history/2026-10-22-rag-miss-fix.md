# RAG 漏检定位与同源条款补全

> 阶段记录：实现、测试数量和失败状态对应当时版本。当前结果见[评测总览](../evaluation.md)，后续修复见[真实 MCP 回归](../mcp-semantic-regression.md)。语义复核采用 AI 辅助方式，未经独立人工盲审。

2026-10-22。本阶段处理金卡、手机开箱、食品券跨品类三个案例，核验并完善同源条款补全。

## 已证实的原因

知识库18份政策、76个片段，三个问题所需条款均存在。使用历史诊断中的三个固定查询逐阶段复现，候选池12、阈值-2、Top3。

| 案例 / 必要条款 | 向量名次 | BM25名次 | RRF名次 | 重排名次 / 分数 | 原因 |
| --- | --- | --- | --- | --- | --- |
| 手机 / return_digital_policy.txt-1 | 9 | 未进前12 | 被融合截断 | 未进入重排 | 单路低位候选在RRF Top12丢失 |
| 金卡 / membership_policy.txt-1 | 3 | 4 | 3 | 6 / 0.7514 | 达到阈值，但被Top3截断 |
| 金卡 / membership_policy.txt-2 | 1 | 1 | 1 | 1 / 5.2560 | 只拿到黑金延长条款，缺金卡限制 |
| 食品券 / coupon_scope_policy.txt-0 | 7 | 8 | 7 | 4 / -0.4696 | 达到阈值，但被Top3截断 |

手机必要条款即使对全库重排也只有第25，分数约-1.9286；只扩大候选池仍会被Top3截断。金卡和食品券降低阈值也无法解决，因为分数本已高于-2。

固定查询分别为：

- 手机：手机拆封后能否退货 七天无理由退货
- 金卡：金卡会员购买普通商品，页面未标注延长，退货申请期是多少天？
- 食品券：食品券在有效期内能否用于购买显示器？优惠券适用商品范围如何规定？

这证明的是这些查询的检索阶段漏检，不能代替修复后真实Agent查询与回答的验证。

## 修复方式与边界

保留原Top3锚点和重排分数，在命中的同一政策文件中补充相邻真实片段，各来源轮流补充，优先距离锚点近的段落，正文预算1800字符。锚点原文本和ID不变；补充条款保留自己的ID与来源，通过context_chunks附加，不能冒用锚点分数。

该方式适合当前短政策被逐行切分、一个结论依赖多条限制的语料。它没有修复底层RRF排序或重排模型能力，而是修复提供给回答模型的证据组装。所有相关政策文件都没被选中时仍会漏检；长文件超出预算仍可能缺证据。1800是补充时检查的正文预算，原始锚点本身超预算时仍保留；JSON、元数据和历史消息不包含在此预算内。

生产涉及src/business/policy_context.py、src/business/rag_retrieval.py、src/agents/policy_evidence.py。补充证据进入下一轮复用检查，仍须由检查器核对新条件，不因为来自同文件就自动认定可回答。

本轮另修正scripts/evaluate_query_rewrite.py的评测口径：原话对照同样展开实际返回的context_chunks，必要证据覆盖也统计这些真实条款，避免只给改写侧计入补证收益。新增集成回归确认补证返回、来源隔离及锚点不变。

## 复现结果

| 固定案例 | 必要证据覆盖：修复前→补全后 | 正文字符：前→后 |
| --- | --- | --- |
| 手机 | 0%→100% | 273→1235 |
| 金卡 | 50%→100% | 264→1017 |
| 食品券 | 0%→100% | 227→721 |

固定查询真实运行新旧检索，确认锚点ID相同。额外116题只运行旧检索得到锚点，再使用相同锚点做上下文补全对照，避免把排序波动算作收益。

| 既有题集 | 有答案题数 | 完整必要证据：原锚点→补全 | 平均正文字符：前→后（含无答案题） |
| --- | --- | --- | --- |
| RAG开发集 | 18 | 17→17 | 170→655 |
| RAG测试集 | 36 | 36→36 | 191→607 |
| 对话诊断集 | 24 | 19→21 | 204→603 |
| 原20题验证集 | 16 | 14→15 | 244→693 |

116题未发现必要证据覆盖下降；无答案题的空返回数量未改变，但这不证明拒答质量不变。原20题验证集已经使用过，本轮属于回归，不能再称全新留出集。本轮数字来自固定原话查询，和旧版真实Agent多轮查询的21/24、12/16不是相同实验口径，不应混用。

62项相关本地unittest通过（评测13、上下文7、检索11、证据与政策工具16、审批10、订单5）。审批测试覆盖暂停无工单、批准前复查、重复与并发批准去重、检查点恢复。它们不能代替网页人工演示。

## 成本与尚未验证的项目

补全文本在本地组装，新增模型调用为0，向量和重排调用次数不变。但正文约从200字符增至600—700字符，固定手机案例约增至4.5倍；输入成本未降低。回答耗时和token用量见 [在线验证](2026-10-22-rag-online-validation.md)。

已有证据检查节点仍每轮调用一次模型，未在本轮改动。问候、订单查询、重复解释是否值得检查，须另行做带调用数、用量、延迟及质量的对照；不能用本轮补证结果回答该问题。

三个案例及相关政策已完成两轮在线验证。真实图三个主要政策结论正确；固定单轮仍有食品券过度拒答，部分回复仍承诺查询工具不存在的签收时间字段，不能称全面稳定。原始失败、提示修改、实际用量及逐条复核见 [在线验证](2026-10-22-rag-online-validation.md)。既有50题的独立人工复核待完成。

## 本地复现

```powershell
# 在D:\VScodeProgects\commerce-desk-agent执行；不调用外部聊天模型
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
.venv/Scripts/python.exe scripts/diagnose_rag_misses.py
$env:PYTHONPATH='src'
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*evaluation.py' -v
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_policy_context.py' -v
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_rag_retrieval.py' -v
.venv/Scripts/python.exe -m unittest discover -s tests/agents -p 'test_policy*.py' -v
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_shipping.py' -v
.venv/Scripts/python.exe -m unittest discover -s tests/agents -p 'test_supportpilot_agent.py' -v
```

本地需要现有索引和模型缓存。诊断基线固定为e71f35fe2a7ce333bd7271a8ddedb57ffa0e5cd1，报告记录基线源码、当前检索/补全源码和语料哈希。原始结果在work/rag-miss-fix-offline.json；本轮发布快照在docs/rag-miss-fix-offline.json。
