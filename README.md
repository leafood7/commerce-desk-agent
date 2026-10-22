# SupportPilot Commerce Agent · 电商智能客服

基于 LangGraph、FastAPI 和 Streamlit 的电商客服项目，支持订单查询、政策问答、催单及人工审批。

## 主要功能

- 模型工具调用与多轮对话，SQLite检查点保存会话及审批流程。
- 本地订单查询、普通催单直接登记、超时加急申请和人工批准/拒绝。
- 审批前重新检查订单，事务与唯一约束防止重复创建工单。
- RAG：BGE向量检索＋中文BM25＋RRF融合＋BGE交叉编码器重排，保留纯向量策略用于对照。
- 18份政策、76个片段；66条内部RAG评测题，以及30条查询改写端到端诊断对话。

## 本地启动

仓库： https://github.com/leafood7/supportpilot-commerce-agent

需要Python 3.12至3.14及uv，安装依赖：

```powershell
uv sync --frozen --no-dev --extra supportpilot
Copy-Item .env.example .env
```

按需配置.env中的模型接口。当前本地使用的兼容接口配置参见[本地运行说明](LOCAL_SUPPORTPILOT.md)，真实Key不要提交到Git。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

网页：http://127.0.0.1:8501 ，API：http://127.0.0.1:8081/docs 。首次使用需要准备嵌入及重排模型；模型、数据库和运行日志不随源码上传。

## 文档与评测

- [本地运行说明](LOCAL_SUPPORTPILOT.md)
- [整体代码导读](docs/supportpilot-walkthrough.md)
- [催单审批流程](docs/shipping-approval-walkthrough.md)
- [政策来源与一致性检查](docs/policy-expansion-review.md)
- [RAG优化与评测](docs/rag-optimization.md)
- [查询改写端到端评测](docs/query-rewrite-evaluation.md)
- [内部评测题集](data/evaluation/README.md)

测试集结果和局限请以评测报告为准。内部题集上的证据命中不代表真实用户回答准确率；模型查询改写仍可能遗漏具体条款。工单保存在本地，未接入真实仓库、快递或退款系统。当前统一工作台没有个人账号隔离。

## 开源来源

本项目基于[JoshuaC215/agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit)二次开发，参考[SupportPilot](https://github.com/Chenmaolin-maker/supportpilot-agent)的电商客服场景，并参考HelpPilot和storekeeper的人工审批设计。
本仓库独立维护，保留上游来源说明与[MIT许可证](LICENSE)；上游使用说明见[原始README](docs/upstream-toolkit-readme.md)。
