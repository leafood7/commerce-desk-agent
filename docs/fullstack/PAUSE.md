# CommerceDesk 暂停断点（恢复前必读）

状态：**用户已于2026-10-28明确要求继续。本断点已恢复并完成本批MCP/RAG本地验证。最新状态见 knowledge-implementation-report.md 和 knowledge-checks.json；下次不能从旧的失败用例断点重复开始。**

## 项目与授权

- 唯一工作目录：D:\VScodeProgects\commerce-desk-agent。
- 分支：feat/fullstack-foundation；起点提交3229b57。大量已有修改和新文件尚未提交，不能reset、clean、覆盖或误当作外来改动。
- 用户选择：**容器验收稍后补，先继续MCP和RAG**。不要重新要求安装Docker/WSL。
- Docker仍未实跑；本机Windows11，MySQL便携版8.4.13可用。
- 本轮外部付费模型调用为0。100元总评测预算不变；旧诊断占用上界4.519364元。不要重置账本。
- 不读取 data/evaluation/answer_trust/holdout.jsonl 的正文，不揭盲24条最终题。

## 上一批已验收基础（历史结果，不能当成本轮新改动已全通过）

118项客服单元回归；135项应用/服务/旧界面回归、2项SQLite跳过；21项真实MySQL；3条Chromium浏览器链路；原stdio MCP8项检查。见docs/fullstack/checks.json和implementation-status.md。它们记录的是上一批基础版本，本轮MCP/RAG修改后还须重新验收。

## 本轮已经写入的实现

### MCP

- tool_contracts.py：严格工具输入；登录会话派生5分钟专用MCPGrant，原登录token不透传给工具服务。
- 新令牌绑定parent_session_hash、资源audience和角色scope；退出/停用/过期后拒绝后续工具请求。
- mcp_server.py：官方低层SDK+Streamable HTTP+认证中间件，独立端口8083。属于第一方工具授权，不宣称实现OAuth服务器。
- 四个工具：get_order_status、get_shipping_status、search_knowledge_base、request_shipping。写工具只能登记普通催单或待审批加急，不能审批，不通知外部仓库。
- mcp_gateway.py：工具白名单、入参/返回校验、超时、关闭上下文后处理权限错误，不静默回退本地。已修复anyio异常组把forbidden掩盖成unavailable的问题。
- runs.py和app.py：查单运行改为真实MCP；新增knowledge_search运行。后台只持有短期工具凭据，不把凭据保存到运行结果。
- **真实本机API→MCP→MySQL查单已验证成功**，output.transport=mcp。

### 文档与检索

- knowledge_models.py：Document、DocumentVersion、KnowledgeChunk、IngestionJob、MCPGrant、ToolInvocation。
- 迁移0002_knowledge已生成并应用到开发库；Database.check_schema和CLI目前期望0002_knowledge。
- document_parsing.py：UTF-8 TXT/MD、文本型PDF、普通DOCX；文件20MB，PDF200页、提取文本100万字符、DOCX归档/XML限制；保留可识别标题、页码、表格行。扫描件/无正文明确失败。
- fixed/structural两种分块，默认600字符/重叠80；Markdown即使没有空行也已处理章节切换。
- knowledge_service.py：按组织/内容哈希/配置去重；不可变版本；待处理→ready后管理员手工发布；单文档活动版本指针；撤回、重试、原文件下载与历史来源查看。
- 未标注适用范围为unknown，不能当作通用政策。显式范围筛选保留unknown和general，排除已确认不适用类别。
- ingestion.py：使用现有MySQL持久作业队列，独立进程；原子领取、5分钟租约、心跳、最多3次、失效租约接管、达到上限转failed。Redis/Celery尚未接入。
- knowledge_index.py：**独立Chroma HTTP服务**端口8005；避免worker和MCP进程共写嵌入式索引。BGE-small-zh-v1.5固定revision 7999e1d3359715c523056ef9478215996d62a620，normalize=True、cosine；重排复用原项目固定revision。
- 真实Chroma heartbeat和已缓存的固定BGE/重排模型加载已验证；没有外部LLM调用。
- retrieval.py：按组织+活动版本集合缓存SQL正文/BM25快照；向量+BM25+RRF+重排；邻段预算4000字符；返回前重新查询当前发布状态。向量服务只提供候选ID/距离，正文以SQL为准，伪造ID不成为依据。
- validated_evidence()在API检索及运行发布前再次核对ID、正文、组织、活动版本和有效日期。
- knowledge_api.py：上传、版本、发布/撤回/重试、原文件、引用与搜索；文档列表已改为批量版本/任务查询。
- policy回复当前是**依据摘录**，不是已完成的LLM自然语言解释，也不是新售后LangGraph闭环。

### 前端

- api.ts、Knowledge.tsx已经写入。
- 三个标签：订单查询、政策检索、知识库。
- 管理员上传/新增版本、人工发布/撤回/重试、下载原文；政策检索通过运行/SSE展示步骤和来源。
- TypeScript/Vite构建已经通过。
- **本轮新增的上传→处理→发布→检索浏览器测试尚未编写/执行**。旧3条浏览器验收不能代替它。

## 暂停时最重要的测试断点

1. 早期SQLite知识库/基础回归：29 passed、2 skipped。
2. 真实HTTP MCP专项：**4 passed**（令牌隔离/过期撤销/audience/角色与写入/跨组织已发布检索）。
3. 最新真实MySQL全套：
   - 命令：python -m pytest tests/workbench -q --tb=short --junitxml=work/fullstack/knowledge-mysql-junit.xml
   - **38 passed，1 failed**，总39项。
   - 失败：test_publication_validator_rejects_forged_and_expired_sources，NameError: User。
   - **源文件中的User导入已经补上**（tests/workbench/test_knowledge.py：from workbench.models import User, now）。
   - 该测试进程在修改前已经完成收集，因此旧进程仍然失败；修复后尚未重新运行。恢复时先新进程重跑这个用例，再运行整套。
4. 当前新代码曾通过Ruff检查/格式化，但之后又有测试导入小改动，恢复后再检查。

## 进程状态（仅作定位，恢复时必须核对归属，不能只凭旧PID杀进程）

用户要求暂停后：
- 文档处理worker及其Python子进程已停止（2个进程）。
- 本轮测试已自然结束，没有继续运行的测试。
- MySQL、Chroma、MCP和API/前端空闲预览保留：
  - 33316 MySQL，暂停时PID29060。
  - 8005 Chroma，PID42876。
  - 8083 scoped MCP，PID43212。
  - 8082 API，PID31300。
  - 5173 Vite，PID12516。
- 这些进程可能加载的是最新修改前的代码；恢复后验证或安全重启本项目进程。
- scripts/start-workbench.ps1、stop-workbench.ps1 **目前仍只管理API/前端**，还没有正式管理MCP/worker/Chroma。
- 临时知识服务进程记录：work/fullstack/knowledge-processes.json。worker PID已经失效，不能盲信。
- 凭据在被忽略的work/fullstack/mysql-local/credentials.json及local-login.json，**不要在工具输出/报告中打印**。
- 上传文件若排队，暂停期间不会自动处理，待恢复worker。

## 恢复后的明确顺序

1. 读本断点，检查git状态与当前文件；保留所有未提交改动。
2. 新进程重跑User导入已修复的失败用例，再跑全套MySQL和SQLite/MCP测试。MySQL测试只准使用commercedesk_test*专用库，不能指向开发库。
3. 完善并验证启动/停止脚本对知识服务的管理；重启当前API/MCP/worker以加载最新代码。不要安装Docker。
4. 为新界面增加Playwright：管理员上传真实文件→worker真实BGE索引→未发布不可检索→发布→政策检索显示原文和来源→撤回后不再检索；同时验证跨商家隔离和原文件访问。
5. 冻结检索开发对照，再运行真实BGE/重排实验。原公开rag_cases.jsonl有22条dev、44条旧公开test；本轮只计划取22条dev，再人工整理18条有意义的条件/问法变体形成约40条。**不要用单纯加前缀凑独立场景**，同家族仍是开发题，不能宣称独立盲测。
6. 建议评测：fixed/structural × vector/hybrid/hybrid_rerank，固定语料/模型/参数，保存逐题候选、必要条款覆盖、无证据误召回和耗时。这是检索服务对照，不是已证明最终回答正确率。必要证据组来自旧开发题evidence_groups，可映射原政策正文。
7. 18份原政策可格式适配为Markdown（标题和段落），两种切块使用完全相同适配语料；记录原文件哈希和适配规则。实验应使用独立SQL库与独立组织/Chroma collection，不扰动demo-a已有数据。该实验库和40题**目前尚未创建**。
8. 更新CI、Compose知识服务目标及文档、执行本轮最终回归。当前Compose仍只有MySQL/API/React，未增加Chroma/MCP/worker；不能声称容器链路已完成。Docker实跑继续延期。
9. 生成本轮独立验收报告，保留上一批checks.json历史意义，不把旧通过项当作新版本通过。
10. MCP/RAG稳定后才进入下一阶段的售后LangGraph与受约束生成；用户未要求暂停前不再反复确认已授权工作。

## 已知未完成/待审事项

- 真实Chroma上传/检索整条链尚未做浏览器验收，虽然模型预热和MCP查单已验证。
- MCP主进程预热失败目前可能阻止服务启动，按需决定是否隔离知识工具故障；不要宣称已实现完善降级。
- MCP工具业务写入与后续审计是两个事务；审计失败时不应掩盖已经执行的业务状态，这点尚未专门处理/测试。
- 服务allowed_hosts及绑定当前以localhost为主，Docker/远程接入需配置化；完整OAuth/OIDC不在已实现范围。
- 解析只有文件/页数/文本边界，尚无容器CPU硬超时。
- 未完成40题检索实验、100文档/一万片段容量验收、Redis/Celery和LangGraph新业务闭环。
- 不要把“引用存在”“原文摘录”“测试替身检索通过”写成模型语义准确率提升。

暂停记录由用户明确要求建立。后续收到“继续”先读此文件，从“User导入修复后的复测”开始。
