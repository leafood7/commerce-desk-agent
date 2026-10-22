# CommerceDesk · 商询

基于 LangGraph、FastAPI 和 Streamlit 的电商客服 Agent，支持订单查询、政策问答、催单与人工审批。

## 在 Toolkit 上新增的能力

- **客服业务**：订单工具、普通催单、加急审批、执行前复查与工单去重。
- **政策 RAG**：BGE 向量检索、BM25、RRF 融合与交叉编码器重排。
- **多轮取证**：检查当前问题和已有证据，不足时重新检索，足够时复用。
- **评测**：检索对比、回答依据检查、条件更正回归及新题配对验证。

## 启动

需要 Python 3.12–3.14 和 uv。

```powershell
uv sync --frozen --no-dev --extra supportpilot
Copy-Item .env.example .env
# 按运行说明填写模型接口配置
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

网页：<http://127.0.0.1:8501>；API：<http://127.0.0.1:8081/docs>。

## 文档

- [配置与运行](LOCAL_SUPPORTPILOT.md)
- [架构与调用链](docs/architecture.md)
- [RAG 检索](docs/rag-optimization.md)
- [评测结果与复现](docs/evaluation.md)
- [政策来源](docs/policy-expansion-review.md)

工单仅保存在本地，未接入真实仓库或退款系统。评测为内部小样本，模型裁判结果不等同真实业务准确率。

## 开源来源

基于 [JoshuaC215/agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit) 二次开发，保留其服务、会话与模型接入基础，新增上述电商业务、RAG 和评测能力。版权与许可见 [LICENSE](LICENSE)，修改说明见 [NOTICE](NOTICE.md)。
