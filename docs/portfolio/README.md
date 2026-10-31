# CommerceDesk 本地作品交付包

定位：面向电商售后的全栈Agent＋RAG工程作品。核心是订单行级退货、政策证据、受限流程决策和独立审批，外部受理明确为本地模拟。

当前代码在 `D:\VScodeProgects\commerce-desk-agent`。默认展示版本以`main`分支为准，开发与审核分支为`feat/fullstack-foundation`；以实际提交为准，不是生产发布版。交付清单记录提交前的本地快照，历史报告中的未提交状态描述当时实验，不是当前分支状态。来源与二次开发说明见根目录LICENSE、NOTICE。

## 按这个顺序查看

|材料|用途|
|---|---|
|[三段演示](demos.md)|8—12分钟展示正常完成、信息不足和分支更正约束|
|[当前架构](architecture.md)|客户端、应用、MCP适配、工具服务、数据与外部服务的实际边界|
|[启动与排障](operations.md)|本机容器启动、准备新数据、复演及常见故障|
|[证据与限制](evidence.md)|每个数字对应哪次实验，哪些结论不能推出|
|[面试讲解](interview.md)|项目介绍、机制追问、可使用的简历表述|
|[交付检查](delivery-checks.json)|本轮三段浏览器实测、材料检查和候选清单|
|[交付报告](delivery-report.md)|本轮价值、实测、实际困难及学习难点|

业务代码重点入口：`src/workbench/returns.py`、`return_observation_graph.py`、`retrieval.py`、`return_context.py`、`mcp_server.py`、`run_worker.py`；页面在 `frontend/src/`。恢复证据见 [完整恢复报告](../fullstack/full-recovery-report.md)。

## 四条主线收口

1. 退货业务：订单行、数量、资格、预览、更正、独立审批、模拟受理与状态核实已贯通。
2. LangGraph：支持观察、合法动作、有限补查补问、质检等待及暂停继续；模型建议可选，默认规则，收益未证实。
3. RAG：已有上传发布、版本范围、混合检索重排、条件例外、分支证据与长文有限规模对照；仍有漏检，未完成独立语义质量确认。
4. 恢复与交付：已完成独立库/文件/空索引恢复及同一待办续办；本包补齐演示、实际架构、运行与讲解材料。

以上代表本地作品闭环完成，不代表线上客服准确率、生产容量或企业生产就绪。没有Linux线上部署、真实退款、完整通用自主ReAct、Redis或跨机高可用。本轮不为展示技术数量新增组件。

源码学习从[亲自演示对应的源码走读](source-walkthrough.md)开始；它是学习材料，不代表已完成个人面试练习。
