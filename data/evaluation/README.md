# 内部评测题集

- `rag_cases.jsonl`：66条，开发集22、测试集44；开发集用于阈值选择，测试集不参与调参。
- `query_rewrite_cases.jsonl`：30条，覆盖口语、关键条件、多轮补充、更正与无答案。
- `policy_refresh_holdout.jsonl`：20条，在代码修改前编写，用于新题验证。

`evidence_groups`组间需共同覆盖，组内允许等价片段替代。无答案题可能返回相关但不足的资料，空结果率不等于拒答正确率。

这些是项目内部题集，包含已讨论失败场景，未做独立专家盲审。方法、结果和运行命令见 [评测说明](../../docs/evaluation.md)。

- `answer_quality_cases.jsonl`：18条（3条已知+15条初轮新增），在本轮修改前冻结，覆盖条件应用、未知券名、无答案、工具能力、问候、纯查单、混合问题和复述。之后用于调整与最终回归，不再称独立留出集。
- `baselines/answer_quality/`：本轮修改前的源码副本及manifest，支持脱离ignored work目录复现。数据和订单fixture均用于演示，不是生产数据。

离线校验：`python scripts/evaluate_answer_quality.py`。外部调用须显式`--online`且获得相应数据外发授权；结果存在时拒绝覆盖，续跑须哈希一致。
