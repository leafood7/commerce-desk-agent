# RAG优化、评测与代码导读

已实现三种可切换策略，当前本机启用 hybrid_rerank。基础语料18份76片段，本次未改写语料以追求分数。订单、审批和展示页面不新增功能。

## 当前运行流程

用户问题 → Agent选择search_knowledge_base工具 → search_policies → BGE向量召回12条 + 中文BM25召回12条 → RRF融合去重保留12条 → BGE交叉编码器重排 → 原始重排logit至少-2的前3条 → 模型根据证据回答并注明来源。

- 中文BM25使用字符一元/二元及英文数字词元，按BM25公式计算；没有引入新的分词库。
- RRF constant=60，融合各路排名，不把向量距离和BM25原始分数直接相加。
- BAAI/bge-reranker-base固定模型版本2cfc18c9415c912f9d8155881c133215df768a70，CPU运行，4线程，512最大长度；权重约1.1GB放在work目录，不进入Git。
- 0.7只用于原始向量基线；-2是重排模型的原始logit阈值，不是概率、正确率或向量距离。
- 按索引正文内容缓存BM25，正文改变即使片段数不变也会重新建立；两路使用同一Chroma快照。
- 不静默伪装模型加载成功；重排不可用时工具如实返回检索错误，先初始化模型或手动切换策略。

## 数据与实验设计

66条内部标注题：54条有答案、12条无具体答案；开发集22条、测试集44条（36有答案、8无答案）。依据主文档主题分组，组间不交叉；每条有参考答案和精确片段编号，允许一组内多个替代证据。

这是开发时依据本项目政策构造、人工规则式核对的内部数据，不是外部人工盲测。测试主题可能与之前的16个冒烟问题接近，不能当作独立真实用户分布。预留测试仅在阈值固定后运行，未根据测试结果再调参。

开发集最初简单均衡命中/空返回的目标选到阈值4，但损害召回；在首次运行测试前改成“证据Recall@3至少90%，再比较无答案空返回比例、MRR”的约束，仅用开发集选定-2。完整开发过程与阈值网格保存在rag-comparison.json。

## 固定测试集检索结果

| 策略 | 有答案证据Hit@3 | MRR@3 | 无答案空返回 | 平均检索耗时 |
|---|---:|---:|---:|---:|
| 原向量＋0.7阈值 | 19/36（52.8%） | 0.514 | 6/8 | 12.5 ms |
| BM25＋向量＋RRF | 35/36（97.2%） | 0.940 | 0/8 | 13.5 ms |
| 混合＋重排，阈值-2 | 36/36（100.0%） | 1.000 | 6/8 | 730.2 ms |

额外消融：取消旧0.7过滤的纯向量Top3为34/36（94.4%），MRR=0.884。因此从19/36到36/36的提升中，旧阈值过严占很大部分，不能全部归功于混合检索或重排。此消融复用原向量候选，不重复测量耗时。

所有有答案题当前仅有一个必需证据组，所以本题集Recall@3与Hit@3相等；指标实现支持多必需组。MRR为第一个相关片段排名的倒数，缺失为0。

耗时是本机CPU上预热后的分段计时之和，少量集合读取和结果封装开销未计入，不包含LLM生成与网络请求；冷启动另外记录。三种方法按固定次序执行、未多轮随机交错复测，毫秒级差异不宜解读成显著性能提升。纯向量候选指标是Top3，混合候选是Top12，不能直接作同K比较。

无答案空返回率不是拒答正确率：比如“会员手续费多少”可能找到相关但不含金额的政策，需要模型继续核实。

## 固定12题回答对比

测试集中预先指定6条有答案、6条无答案题，三种策略使用相同回答提示词、temperature=0和模型。共生成36份回答并用模型裁判评价，实际模型元数据为deepseek-flash。此实验直接给定检索证据，隔离检索对生成的影响，不包括Agent改写查询或工具选择。

| 策略 | 模型裁判判正确 | 模型裁判判证据一致 | 无答案拒答 |
|---|---:|---:|---:|
| 原向量 | 11/12 | 12/12 | 6/6 |
| 混合 | 12/12 | 12/12 | 6/6 |
| 混合＋重排 | 12/12 | 12/12 | 6/6 |

回答和裁判使用同模型家族，分数有偏差风险，12题很小；不等于经过独立人工审核的真实正确率。原向量在标准化预售电脑能否无理由退货的问题上缺证据而拒答；混合与重排找回证据后给出解释。完整回答、引用片段、裁判理由见rag-answer-comparison.json。

## 端到端验证与剩余问题

本地真实Agent已使用新策略：预售定金不等于全款能检索到预售条款；黑金会员会费未收录时拒绝给具体金额。手机开箱的实测中，模型改写查询后返回通用退货和卫生用品片段，未取到专门数码条款，因此回答不如固定问题检索完整。这说明本次检索基准的36/36不能代表整条Agent链都正确；后续应单独评测查询改写和上下文引用。该失败保留在work/rag-live-upgrade.json，未修改提示词或测试标签掩盖它。

## 运行与回退

初始化：`.venv/Scripts/python.exe scripts/init_reranker.py`。启动脚本会按当前策略检查模型缓存；请求期间只从本地加载，不临时下载。

在.env中设置下列值后重启服务：

```dotenv
SUPPORTPILOT_RETRIEVAL_STRATEGY=hybrid_rerank
SUPPORTPILOT_RAG_POOL_SIZE=12
SUPPORTPILOT_RERANK_THRESHOLD=-2
```

策略可改为vector回到旧方案，或hybrid仅启用BM25＋RRF。不要根据测试集表现继续改阈值；后续调参需要新增独立验收题。

复现实验（PowerShell先设置 `$env:PYTHONPATH="src"`）：

```text
.venv/Scripts/python.exe scripts/evaluate_rag.py --split dev --calibrate --output work/rag-dev.json
.venv/Scripts/python.exe scripts/evaluate_rag.py --split test --threshold -2 --output work/rag-test.json
.venv/Scripts/python.exe scripts/evaluate_rag_answers.py
```

前两条使用本地模型；第三条调用当前LLM接口，需要已有Key并产生接口用量。原始候选、模型分数、逐题结果在work/rag-dev.json与work/rag-test.json，摘要在docs/rag-comparison.json，数据及配置在data/evaluation/。

## 按流程读代码

1. src/agents/supportpilot_agent.py 的 search_knowledge_base：工具入口。
2. src/business/knowledge.py 的 search_policies：选择策略、获取索引、保存检索日志。
3. src/business/rag_retrieval.py 的 retrieve：组织两路召回、融合和重排。
4. BM25Index.search：关键词评分；reciprocal_rank_fusion：融合排名；rerank：对问题和片段联合评分。
5. scripts/evaluate_rag.py：加载标注、检查索引一致、计算指标；evaluate_rag_answers.py：回答与模型裁判对比。

参考：[BGE模型说明](https://huggingface.co/BAAI/bge-reranker-base)、[Sentence Transformers CrossEncoder](https://www.sbert.net/docs/cross_encoder/usage/usage.html)。

## 回归验证
47项相关单元/回归测试通过，覆盖中文BM25、RRF去重、重排阈值、题集证据有效性与禁止测试集调参，以及原订单查询、催单、审批和政策工具。检查中发现原有两处学习注释放在SQL字符串内导致SQLite语法错误，已将注释移到Python注释行，保留原说明；未改变业务规则。
