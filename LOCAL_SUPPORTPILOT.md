# CommerceDesk 配置与运行

## 安装

```powershell
uv sync --frozen --no-dev --extra supportpilot
Copy-Item .env.example .env
```

在 `.env` 配置兼容 OpenAI 协议的模型接口：

```dotenv
DEFAULT_MODEL=openai-compatible
COMPATIBLE_BASE_URL=https://api.deepseek.com/v1
COMPATIBLE_MODEL=deepseek-chat
COMPATIBLE_API_KEY=填写自己的密钥
DATABASE_TYPE=sqlite
HOST=127.0.0.1
PORT=8081
SUPPORTPILOT_RETRIEVAL_STRATEGY=hybrid_rerank
SUPPORTPILOT_RAG_POOL_SIZE=12
SUPPORTPILOT_RERANK_THRESHOLD=-2
```

密钥仅保存在本地；`.env`、数据库、模型缓存和运行日志已被 Git 忽略。

## 启动与停止

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

启动脚本初始化订单、政策索引和重排模型，再启动 API 与网页。首次下载模型需要网络；保持终端运行，按 Ctrl+C 停止。8081 或 8501 已占用时先停止占用进程。

- 网页：<http://127.0.0.1:8501>
- API：<http://127.0.0.1:8081/docs>
- 日志：`work/service.stderr.log`、`work/web.stderr.log`

可输入“查询订单 A1002”“银行卡退款多久到账”“请催 A1001 发货”。加急申请是否可提交取决于订单状态和承诺时间；提交后在网页审批区处理。

## 数据与索引

订单库：`data/supportpilot/orders.sqlite3`；政策索引：`data/supportpilot/chroma/`；模型缓存：`work/huggingface/`。

更新 `data/knowledge/` 后重建索引：

```powershell
.venv/Scripts/python.exe scripts/init_knowledge.py
```

该工作台没有个人账号隔离，适用于本地运行；工单登记不代表已通知真实仓库。
