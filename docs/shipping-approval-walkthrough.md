# 催单与加急审批实现说明

本阶段保留 agent-service-toolkit 作为底座，借鉴 HelpPilot 的客户/客服交互、storekeeper 的程序规则校验思想。没有复制这两个项目的代码。

## 业务规则
- 单纯查询物流不写数据库；缺少订单号先询问。
- 用户明确要求普通催单：已付款且待发货即可直接登记本地工单。
- 用户明确要求加急：已付款、待发货、超过订单的 promised_ship_at，生成待审批申请。
- 客服批准前重新读取订单。已发货、未付款、截止时间变更为未来，申请失效。
- 拒绝只记录结果与原因，不创建加急工单。
- 本地工单不代表仓库收到通知，也不会退款或修改订单物流状态。

## 保存在哪里
- orders：新增 paid_at、promised_ship_at，均保存带时区时间。
- shipping_requests：申请类型、原因、状态、审批时间、备注、执行结果。
- shipping_tickets：实际本地工单；request_id 唯一。
- orders-approvals.sqlite3：独立的 LangGraph 审批检查点。
- 初始化只补充缺少的时间，不重置已有订单。演示订单首次按已付款小时数和48小时承诺生成时间；实际判断读取每张订单的截止时间，与政策检索分离。

## 调用链
聊天 → supportpilot_agent.request_shipping → shipping.submit_shipping
→ submit_request（校验、数据库事务、防重复）
→ 普通：写入工单，直接返回
→ 加急：run_approval → review → interrupt（保存检查点）

网页 → shipping_ui → POST /supportpilot/shipping/requests/{id}/decision
→ run_approval → Command(resume=...) → review
→ execute_decision（重新查订单、事务内检查状态、写入工单及审批结果）

客户聊天和审批使用两张不同的图，客户消息不会恢复审批图。
数据库事务 BEGIN IMMEDIATE 和唯一约束保护重复/并发批准。数据库记录是执行结果的权威来源；检查点恢复后再次进入执行函数也不会重复建单。

## 阅读顺序
1. src/agents/supportpilot_agent.py：找到 request_shipping，理解模型只负责选择工具和参数。
2. src/business/shipping.py：先读 eligibility，再读 submit_request；普通催单在这里完成。
3. 同文件 run_approval、approval_graph：理解暂停及恢复，审批输入来源是后台接口。
4. 同文件 execute_decision：真正执行审批、创建工单的位置。
5. src/service/shipping_api.py：网页请求如何进入业务函数。
6. src/shipping_ui.py：列表、批准、拒绝按钮。
7. src/business/order_repository.py：表结构及兼容旧数据库的初始化。

## 验证
PYTHONPATH=src，运行 python -m unittest discover -s tests -p test_shipping.py -v。
包含普通催单、未超时、无效订单、未付款、已发货、拒绝、批准前复查、并发批准、持久化恢复等用例。

## 当前边界
- 当前使用无个人登录的统一工作台，访问者可以审批，无客户账号隔离。可选 AUTH_SECRET 仅保护整个服务。
- 每个订单每种申请只保留一条，拒绝后暂不重新申请；这是第一版的去重范围，不是通用的售后生命周期设计。
- 不自动推送审批结果到客户消息；客服台可查看结果，客户再次询问同类申请会返回已有结果。
- 没有真实仓库/快递接口，没有独立后台任务和消息通知。

参考：https://github.com/poysa213/HelpPilot
参考：https://github.com/Rahat-Kabir/store-keeper
