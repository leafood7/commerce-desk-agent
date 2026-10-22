# 本地 SupportPilot 网页演示

当前支持订单查询、政策 RAG 问答与来源引用；未迁移物流工作流或人工审批。

订单模块、演示数据和数据库均位于当前项目内，无需下载或安装原 SupportPilot。

新环境安装（需要 uv，或先安装 uv）：

```powershell
uv sync --frozen --no-dev --extra supportpilot
```

`supportpilot` 可选依赖包含运行 BGE 所需的 sentence-transformers。

## 启动

首次使用也可以单独初始化订单数据库：

```powershell
cd D:\VScodeProgects\supportpilot-commerce-agent
.\.venv\Scripts\python.exe scripts/init_demo_orders.py
```

初始化将 `data/demo_orders.json` 中的 A1001～A1004 写入
`data/supportpilot/orders.sqlite3`。重复运行只补充缺少的订单，不覆盖已有订单。
查询以只读连接访问数据库；数据库缺失时会提示初始化，不会偷偷创建空库。

在 PowerShell 中执行，并保留终端运行：

```powershell
cd D:\VScodeProgects\supportpilot-commerce-agent
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1
```

该命令仅为本次启动脚本设置执行策略，不更改系统执行策略。若端口已占用，先复用已经运行的页面。
启动脚本也会执行上述初始化，无需重复手动执行。
首次启动还会建立政策索引，BGE 模型首次下载需要网络；已有索引会跳过建库。

- 网页：http://127.0.0.1:8501
- API 文档：http://127.0.0.1:8081/docs
- 默认 Agent：`supportpilot`
- 模型：通过兼容接口调用 `deepseek-chat`
- 停止：在启动终端按 Ctrl+C。
- 日志：`work/service.stderr.log`、`work/web.stderr.log`。

`.env` 中的 `COMPATIBLE_API_KEY=${DEEPSEEK_API_KEY}` 引用启动终端已有的环境变量。
如果新终端没有该变量，请在本地 `.env` 中填写自己的 `COMPATIBLE_API_KEY`，不要提交密钥。

## 使用

在网页输入：`查询订单 A1002`。

应看到工具调用 `get_order_status`，并回答：机械键盘、已付款、运输中、预计明天送达。
输入 `查询订单 A9999`，应返回未找到订单。

政策问题示例：

- `银行卡退款多久到账？`：应回答 3～7 个工作日，并引用 `refund_policy.txt`。
- `签收后多少天内可以申请电子发票，提交后多久开具？`：应回答 30 天、2 个工作日，引用 `invoice_policy.txt`。
- `你们公司创始人是谁？`：知识库没有依据，应明确无法确认。
- `黑金会员每年需要交多少元年费？`：有会员权益资料，但没有年费金额，不应猜测。

这些政策和订单均为学习演示数据。

## 更新政策

文件位于 `data/knowledge/`。第一行作为标题，之后每个非空行作为一个正文片段。
修改、增加或删除文档后，手动重建：

```powershell
.\.venv\Scripts\python.exe scripts/init_knowledge.py
```

索引位于 `data/supportpilot/chroma/`，模型缓存位于 `work/huggingface/`。
重建会更新同编号片段并移除已删除片段，空语料会报错并保留旧索引。
可用 `.env` 中的 `SUPPORTPILOT_CHROMA_PATH` 配置索引路径。
启动只检查索引是否就绪，不会自动检测政策文件变动。

本地演示没有接入用户身份与订单归属校验，仅在回环地址监听。

## 新增及修改文件

- `src/agents/supportpilot_agent.py`：LangGraph 图和订单工具。
- `src/business/order_repository.py`：项目内部的 SQLite 初始化与订单查询。
- `src/business/knowledge.py`：文档切块、BGE 向量化、Chroma 检索、距离过滤和日志。
- `data/knowledge/`：5 份政策文档。
- `scripts/init_knowledge.py`：重建政策索引。
- `data/demo_orders.json`：可随代码分发的虚构演示订单。
- `scripts/init_demo_orders.py`：初始化演示订单的命令。
- `src/agents/agents.py`：注册 `supportpilot`，设置为默认 Agent。
- `scripts/start-local.ps1`：启动本地 API 和网页。
- `tests/agents/test_supportpilot_agent.py`：工具参数、查询及错误分支测试。
- `tests/test_order_repository.py`：使用临时数据库验证初始化、重复运行和查询行为。
- `tests/test_knowledge.py`、`tests/agents/test_policy_tool.py`：知识库与工具测试。
- `docs/supportpilot-walkthrough.md`：从启动、订单到政策问答的逐步代码导读。
- `.env`：本机配置，被 Git 忽略。

原 SupportPilot 项目保留。本项目不再动态加载外部文件，也不读取外部数据库。
业务场景和示例订单沿用前面的 SupportPilot 学习练习，订单模块已整理到本项目。
本地订单数据不包含原项目后续创建的工单或修改。
可在 `.env` 用 `SUPPORTPILOT_DB_PATH` 自定义订单数据库路径；相对路径以项目根目录为准。
不再需要 `SUPPORTPILOT_ROOT`。

## 调用链

```text
Streamlit 网页 src/streamlit_app.py
  → AgentClient.astream() / ainvoke()（取决于网页是否启用流式输出）
  → FastAPI /supportpilot/stream 或 /supportpilot/invoke
  → LangGraph model 节点：DeepSeek 选择订单工具
  → tools 节点：get_order_status(order_id)
  → business/order_repository.py 的 get_order_from_db(order_id)
  → 当前项目的 data/supportpilot/orders.sqlite3
  → 工具结果返回 model 节点
  → 生成回答，服务将结果传回网页
```

政策分支：model → search_knowledge_base → search_policies → BGE/Chroma
→ 距离过滤 → 返回带来源的片段 → model 组织回答 → 网页显示。

检索候选及距离记录在 `work/policy-retrieval.jsonl`。
工具返回 `found`、`no_evidence` 或 `error`，提示词要求核对证据是否回答问题；
距离阈值不能保证事实正确，引用和拒答仍需要独立评测。

会话检查点由 toolkit 自己的 `checkpoints.db` 保存，与订单业务数据库分开。

## 本次验证

- 工具单元测试：5 项通过，使用模拟数据库，不调用真实模型。
- 数据库测试：6 项通过，使用临时数据库。
- 知识库单元测试：9 项通过；政策工具测试：3 项通过。
- 实际 BGE/Chroma 检索检查：5 条有答案问题、1 条无答案问题通过。
- 覆盖重复初始化不覆盖已有数据、查询不存在订单、不创建空库等情况。
- 服务启动时将旧 SUPPORTPILOT_ROOT 设为不存在的目录，查询仍成功。
- 网页查询 A1002：显示工具调用和真实订单字段。
- SupportPilot API 查询 A9999：如实返回未找到。
- API 和网页健康检查：HTTP 200。

未运行全部上游测试，也未验证上游其他 Agent 的外部服务集成。
以上都是当前小样本验证，不代表生产准确率。

测试命令：

```powershell
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m unittest discover -s tests/agents -p test_supportpilot_agent.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_order_repository.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_knowledge.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/agents -p test_policy_tool.py -v
```


## 催单与加急审批
已增加普通催单直接登记、超时加急申请、网页批准/拒绝、审批前复查与持久化恢复。详细业务规则、边界及阅读顺序见 [催单审批导读](docs/shipping-approval-walkthrough.md)。


## 当前展示入口
登录及独立客服页已移除，使用统一工作台。详见 [统一工作台说明](docs/presentation-workspace.md)。


## RAG混合检索与重排
已增加三策略切换、66条内部评测题、固定测试集对比和生成对比。配置、指标限制、运行命令和调用链见 [RAG优化说明](docs/rag-optimization.md)。
