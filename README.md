# CommerceDesk · 商询

面向电商售后的全栈Agent＋RAG工作台。主线是订单行级退货：政策核对、条件补问、更正、预览确认、独立审批和模拟受理。使用React/TypeScript、FastAPI、LangGraph、HTTP MCP、MySQL、Chroma和Docker。

**[作品交付入口](docs/portfolio/README.md)** · [三段演示](docs/portfolio/demos.md) · [实际架构](docs/portfolio/architecture.md) · [启动排障](docs/portfolio/operations.md) · [证据与限制](docs/portfolio/evidence.md)

## 已实现的完整链路

- 上传TXT/MD/PDF/DOCX，异步解析、结构切块、向量化，管理员发布/撤回政策版本。
- BGE向量＋BM25召回、RRF融合、重排与来源复核；区分规则问答和办理分支的证据组装，保留条件、例外和更正的开发对照。
- LangGraph根据工具观察选择有限动作，支持补查补问、原话确认、等待独立质检及中断恢复；模型建议可选，默认规则路径。
- 退货落实到订单行、数量、资格与版本预览；独立角色审批后仅模拟受理，不执行真实退款。查单、催单和会话/受控配置版本继续保留。
- React通过SSE展示实际步骤和结果；MySQL保存业务、运行、持久队列与图检查点，HTTP MCP工具有严格输入和角色作用域。
- 本地Docker链路及独立恢复已验证：数据库/文件校验、空向量重建、同一暂停待办继续办理和重启后状态保留。

## 本机演示

当前工程：`D:\VScodeProgects\commerce-desk-agent`。默认展示版本以`main`分支为准，`feat/fullstack-foundation`用于开发与审核；请以实际Git提交为准。实验清单保留当时的本地候选快照，不是发布标签。

在已经配置好的本机Docker环境中：

```powershell
cd D:\VScodeProgects\commerce-desk-agent
powershell -ExecutionPolicy Bypass -File scripts/run-container-acceptance.ps1 -Phase Start
.venv\Scripts\python.exe scripts/run_portfolio_demo.py
```

页面：http://127.0.0.1:15173；API：http://127.0.0.1:18082/docs。演示命令新增隔离演示数据并重放三条浏览器场景，不调用付费模型。新机器依赖、私有配置、密码、D盘缓存和手动演示见 [运行说明](docs/portfolio/operations.md)，不要公开整个work目录。

## 如何看待验证结果

新增[真实政策语义复核与一万片段验收](docs/fullstack/rag-real-scale-report.md)：5份官方来源摘要、48份模型回答；100篇合成文档经实际上传入库达10000片段。包含语料缺项、跨渠道混用与题面缺陷，不以引用有效宣称回答正确。

已有长文/有限规模RAG的768条检索记录、720条固定证据组装回放，以及真实页面和恢复后续办证据。各批次存在重叠，不能累计成独立样本或回答准确率；作者参与复核，未做独立盲审。检索改善、退化、成本和原始结果见 [证据索引](docs/portfolio/evidence.md)。

默认不启用模型建议或跨条款引用扩展：模型策略收益未证实，RAG仍存在漏检。当前是本地工程作品，没有真实退款、Linux线上部署、企业生产容量、Redis长期记忆或通用无限自主Agent承诺。

## 开发与历史入口

|文档|内容|
|---|---|
|[全栈说明](docs/fullstack/README.md)|原生兼容启动、API、数据库、Docker与各模块|
|[当前状态](docs/fullstack/STATUS.md)|最新验收及历史阶段记录|
|[退货业务](docs/fullstack/returns.md)|领域规则、审批及模拟受理|
|[受限Agent](docs/fullstack/return-agent.md)|动作集合、观察与实验开关|
|[长上下文](docs/fullstack/long-context-report.md)|完整必要输入、有界历史摘要、订单隔离及容器验证|
|[长文RAG](docs/fullstack/rag-long-scale-report.md)|切块、范围、引用补全及有限规模对照|
|[完整恢复](docs/fullstack/full-recovery.md)|独立库/文件/索引和同一待办续办|
|[面试讲解](docs/portfolio/interview.md)|机制取舍、可核实业绩与限制|
|[旧版运行](LOCAL_SUPPORTPILOT.md)|原Streamlit、stdio MCP与SQLite演示|
|[旧版评测](docs/evaluation.md)|原回答可信度、历史模型与回归记录|

原Streamlit服务是保留的兼容入口，不与新工作台的账号、数据、授权或部署拓扑混用。原回答契约/语义后审实验见 [answer-trust](docs/answer-trust/README.md)，不代表新工作台默认启用该架构。

## 来源与使用范围

基于 [JoshuaC215/agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit) 二次开发，沿用服务、会话与模型接入基础，增加电商业务、证据约束、MCP工具与全栈工作台。版权及修改说明见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE.md)。订单与政策用于隔离演示，未接入真实电商平台、仓库、退款或开票系统。
