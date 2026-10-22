# SupportPilot 统一工作台

当前界面无需个人账号登录，同页提供聊天、催单申请和人工审批。
移除了独立客服页、账号登录和按客户隔离订单/历史的功能；仍可使用 toolkit 原有可选 AUTH_SECRET 保护整个服务。

保留：普通催单自动登记、超时加急申请、LangGraph 审批暂停/恢复、执行前重新检查订单、数据库事务和防重复工单。

展示界面使用“SupportPilot 智能客服”，不使用 demo、演示版等标签。
真实能力边界：订单与工单来自本地 SQLite；未接入外部仓库、快递或真实退款。统一工作台的访问者可以进行审批，不再声称存在独立客服身份。

项目基于 JoshuaC215/agent-service-toolkit 二次开发，参考 SupportPilot 的客服场景；保留原项目 LICENSE 与 README 中的来源说明。
登录模块源码和旧权限测试已移至 work/removed-login-feature 留作可恢复备份；原有账号相关数据表不参与当前流程，未删除旧数据。

阅读顺序：src/agents/supportpilot_agent.py → src/business/shipping.py → src/service/shipping_api.py → src/shipping_ui.py。
