# 第一阶段：错误诊断与路线选择

**第一阶段执行及归档收尾已完成。** 查看[完成记录与后续想法](completion.md)。未决复核范围继续保留，不代表候选机制已验收。

状态：真实运行与单因素对照已完成；**用户已确认018、009、015三个首轮案例的错误归类**。重复运行、配对结果的逐条语义评分及其他争议仍未全部人工复核。执行日期为2026-10-28。此次只增加独立诊断入口和记录，没有修改客服、检索或审批的运行机制，也没有推送本阶段改动。

- [诊断报告与下一阶段建议](report.md)
- [需要作者复核的关键片段](review.md)
- [用户原文确认记录](author-review.json)
- [冻结样本](cases.json)、[配置和来源哈希](manifest.json)、[资源汇总](summary.json)
- [单因素对照定义](interventions.json)、[累计预算快照](budget-snapshot.json)

## 实际执行

使用冻结基线 `571af81e15696fc278e555e39d8694ca4a051572` 的原始Agent、证据检查和回答约束。加载前逐文件检查当前业务、MCP和模型接入源码与基线一致，实际生成调用的提示、消息、工具定义和返回均保留。

模型为 `deepseek-flash`，非思考、温度0、输出上限2048、SDK不自动重试。接口只返回模型别名，不能声称固定了不可变权重版本。检索采用 `hybrid_rerank`，候选池12、阈值-2、top-k=3、补证预算1800字符。模型和索引缓存来自原项目配置。

订单库与索引复制到独立临时目录。订单的时间字段由冻结时钟生成，逻辑数据快照保存在每个MCP结果文件中。写工具是阻断替身；本次没有写入尝试。

`new-name` 是**显式空证据注入对照**：仍调用真实MCP，再将交给Agent的政策结果替换为空。传输原始结果和实际交付结果同时保存，不能把它当成自然发生的空召回。

第一批和第一次重复各发生一次网络错误；原始失败与未知用量预留均保留，续跑使用新文件并记录来源。随后改为每次请求使用新连接，避免复用失效代理连接；模型、提示和采样参数没有改变。没有通过重新运行抹掉失败。

## 复现入口

先安装锁定依赖、准备检索模型与索引，并在环境中配置 `DEEPSEEK_API_KEY`。密钥不会写入结果。

```powershell
uv sync --frozen --extra supportpilot
.venv/Scripts/python.exe scripts/diagnose_answer_failures.py validate
```

`prepare` 仅用于首次生成诊断清单；清单存在时只做校验。`validate` 不调用模型，也不读取最终测试集。以下为真实调用命令示例，需使用新输出文件：

```powershell
.venv/Scripts/python.exe scripts/diagnose_answer_failures.py baseline --online --output work/diagnosis-new-baseline.json
.venv/Scripts/python.exe scripts/diagnose_answer_failures.py baseline --online --ids ablation-018,ablation-009,ablation-015 --repetition 2 --output work/diagnosis-new-repeat.json
.venv/Scripts/python.exe scripts/diagnose_answer_failures.py pairs --online --spec docs/diagnosis-v1/interventions.json --output work/diagnosis-new-pairs.json
```

实际执行命令和对应文件见 `execution-log.json`。配对实验固定已保存的首次真实历史与最终生成请求，两组均禁止新增工具调用，按“原始→干预”“干预→原始”交替顺序运行；它不是重新运行完整Agent。

预算统一累计在 `work/answer-trust/budget.json`，本阶段20元并纳入全项目100元，最多40个回答槽（失败或中断也占槽）。**本批已占36槽，以上命令不是要求现在再次运行**；完整重跑会触及剩余额度，应另行安排。不得删账本或换运行目录冒充尚未花费。

## 测试与限制

```powershell
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -m unittest test_answer_diagnosis -v
```

6项测试覆盖累计预算、未知用量、回答上限、冻结题保护、最终题不被读取及单因素修改边界；不是模型语义正确性测试。原候选分支的完整评测工具仍未完工，本阶段使用独立的 `diagnose_answer_failures.py`。

所有题均为已知历史／开发样本。没有读取24条最终测试题，没有新增模型裁判调用，没有独立盲审；结论仅是诊断假设和下一阶段试验依据。
