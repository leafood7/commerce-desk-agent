# MCP 与版本化知识库：本地运行和边界

本批次接续全栈基础，代码位于 D:\VScodeProgects\commerce-desk-agent。验收结果见 knowledge-checks.json；首批 checks.json 是历史结果，不能代替本批次。

## 实际链路

React → 认证 API → 派生短期工具凭据 → Streamable HTTP MCP → 本商家业务服务 / 检索服务 → MySQL 与独立 Chroma。文件由 API 保存，独立 worker 从 SQL 持久队列领取、解析、分块并索引；管理员核对后发布。

知识检索当前展示来源摘录，不是已接入自然语言生成或新售后 LangGraph Agent。原项目的旧 Agent 保留。

## 本地启动

先配置独立数据库和迁移、演示账号，见 README.md。凭据只存本机 .env 或被忽略的 work/ 文件，不应复制到报告。

```powershell
$env:PYTHONPATH='src'
# 只检查已经缓存的固定版本，默认不联网。
.venv\Scripts\python.exe scripts/prepare_workbench_models.py
# 首次缺缓存时，操作者可显式允许下载；不是付费 LLM 调用。
.venv\Scripts\python.exe scripts/prepare_workbench_models.py --download
powershell -File scripts/start-workbench.ps1
# 统一停止本次启动的服务；保留共享 MySQL。
powershell -File scripts/stop-workbench.ps1
```

启动顺序：MySQL 检查 → Chroma:8005 → MCP:8083 → 文档 worker → API:8082 → 前端:5173。进程记录包含所属目录、启动时间和 PID，停止前检查归属；PID 不能作为唯一依据。日志在 work/fullstack/。不再使用旧 knowledge-processes.json 管理新进程。

-SkipFrontend 可只启后端；-SkipKnowledge 只用于连接已另行运行的工具/知识服务或基础 API 调试，不能将其作为完整查单/检索演示。本地地址显式加入 NO_PROXY；MCP gateway 不继承系统 HTTP 代理。

## 演示步骤

1. 管理员进入“知识库”，上传 TXT、Markdown、文本 PDF 或普通 DOCX，选择人工确认的适用范围和分块方式。未确认就保留 unknown。
2. 等待“待发布”，核对原文件和解析警告。存在缺失 PDF 页时，明确确认后才允许发布。
3. “政策检索”输入问题，通过 SSE 查看检索步骤与来源卡片；来源包含文件、版本、页码或章节，可以下载原文件。
4. 撤回后再次检索，该版本不能进入结果；旧来源可用于审计，但不能作为当前允许证据。
5. 换另一商家登录，不应读到原商家文档、引用或原文件。

文本限制：20MB；PDF200页；正文100万字符；DOCX归档和XML设有限制；每文档最多2500片段。扫描件需要额外 OCR，当前不自动调用 OCR。结构化解析保留基础标题、段落和表格行，不承诺复杂版式还原。

## 工具与业务边界

四个工具：get_order_status、get_shipping_status、search_knowledge_base、request_shipping。schema 拒绝多余参数；scope由当前服务端角色确定，用户不能传 organization_id 指定别的商家。登录令牌不直接传给 MCP；派生凭据绑定登录会话、有效期、角色和资源地址，退出后撤销。

写工具只能登记普通催单或待审批加急，不能审批或通知外部仓库。工具业务已提交后审计写入失败，返回真实业务结果并标记 audit_status=unavailable；日志记录故障，不能把操作显示为未执行。尚未实现可靠审计补写队列。

这是第一方授权，不是已完成 OAuth/OIDC 服务器。远程生产部署还需 HTTPS、密钥与身份管理和运维验证。

## 检索实验

```powershell
$env:PYTHONPATH='src'
.venv\Scripts\python.exe scripts/evaluate_workbench_rag.py validate
# 只允许独立 commercedesk_test_rag_eval，账号需预先有权限。
$env:WORKBENCH_RAG_EVAL_MYSQL_URL='<本机独立实验库连接>'
$env:WORKBENCH_RAG_EVAL_PASSWORD='<隔离实验演示密码>'
$env:NO_PROXY='127.0.0.1,localhost'
.venv\Scripts\python.exe scripts/evaluate_workbench_rag.py run --output work/fullstack/rag-experiment/new-run
# 可选 --ids 以逗号分隔；无效编号直接拒绝。输出目录不可覆盖。
```

data/evaluation/workbench_rag/ 下冻结40条开发题、语料、必要条款和哈希。22条原公开开发题加18条条件改写，同一家族不是独立测试；没有读取 answer_trust/holdout.jsonl。fixed/structural × vector/hybrid/hybrid_rerank 六组交替运行。所有组使用相同600/80分块参数、top3锚点与4000字符上下文预算；来源覆盖和完整条款字符串覆盖单独统计，展开上下文与锚点指标分开。完整条款字符串覆盖是保守的文本指标，不能识别所有等义或跨重叠边界表达，也不能证明回答正确。

实验直接运行真实检索服务；浏览器另行验证完整 MCP 传输链路，不声称240次检索均经HTTP。本实验不评估生产准确率、统计显著性或独立人工盲审。没有新增模型付费调用。

## 容器准备与尚未完成

Compose现包含MySQL、API、Chroma、MCP、worker、Web六个服务。API仍使用轻量workbench target；知识组件使用knowledge target。文件卷由API写、worker只读；向量服务独立持久卷，不暴露宿主端口。模型缓存挂载到/app/work/huggingface/hub，默认离线，可用WORKBENCH_MODEL_CACHE指定准备好的宿主目录。MCP容器不预热模型，使查单不依赖模型缓存；知识检索或worker缺模型会明确失败。

本机未安装Docker/WSL，用户已决定稍后补容器实机验收；这里只验证配置解析和本地多进程链路，没有声称镜像成功构建或服务器部署成功。

后续：新售后LangGraph状态机与受约束生成、Redis会话/缓存、上下文压缩与模型超时/费用控制、100文档/一万片段容量验证、Docker运行与恢复演练。不要把原文摘录界面当作这些能力已经完成。
