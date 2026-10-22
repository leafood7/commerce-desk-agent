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
SUPPORTPILOT_POLICY_CHECK_MODE=auto
SUPPORTPILOT_TOOL_BACKEND=mcp
SUPPORTPILOT_MCP_TIMEOUT=60
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

## 检查模式与演示

`auto`只对完整匹配的独立问候、致谢及明确纯查单省去证据检查；不会跳过实际订单查询。混合问题、条件更正与重复解释仍检查。改为`always`可恢复每轮检查，修改后重启服务。

三条展示流程与面试说明见docs/demo-and-interview.md。审批可运行scripts/demo_shipping_workflow.py，使用独立临时数据库验证暂停、批准前复查与重复批准去重，不改当前订单库。

## MCP 工具后端

API 默认启动本地 stdio MCP 子进程，复用订单和政策工具；模型缓存已准备时启动仍需预热。改为 `SUPPORTPILOT_TOOL_BACKEND=local` 并重启可回退进程内工具。催单与人工审批保持本地执行。独立客户端验证和协议边界见 [MCP 文档](docs/mcp-tools.md)。
