# 持久运行队列：本地运行与排错

项目：D:\VScodeProgects\commerce-desk-agent。当前推荐演示使用已验收的本地 Docker 栈：工作台 http://127.0.0.1:15173 ，API http://127.0.0.1:18082 。未部署服务器。

## 架构与边界

POST创建/继续运行 → 同一MySQL事务写Run/FlowAction及RunJob → 独立run-worker领取 → 重新验证登录会话/当前角色并生成短期MCP授权 → LangGraph或查单执行 → 公开事件与真实结果落库。

job保存父登录会话哈希和动作标识，不保存Bearer明文或模型密钥。原登录过期、注销或用户停用后，作业不能偷偷绕过权限继续。授权过期的售后流程可重新登录后恢复，初始执行尚无checkpoint也能重试；只读查单可重新发起。

本次沿用MySQL做持久工作队列，没有Redis/Celery，也不是独立事件总线。Compose中新增run-worker（256MiB），原worker仍负责文档索引，两者职责不同。默认单个执行worker；并发领取测试不等于已经验证多副本生产容量。

## 启动与验证

在项目目录执行：

~~~powershell
powershell -NoProfile -File scripts/run-container-acceptance.ps1 -Phase Config
powershell -NoProfile -File scripts/run-container-acceptance.ps1 -Phase Build
powershell -NoProfile -File scripts/run-container-acceptance.ps1 -Phase Start
powershell -NoProfile -File scripts/run-container-acceptance.ps1 -Phase Test
~~~

Start进行向前迁移和演示账号幂等初始化，不重置已有密码；迁移0004只新增调度表及索引。不得将现有业务库随意降级。私有验收配置在work/docker/acceptance.env，不打印/提交此文件。Docker镜像、数据盘与缓存位于D盘。

Compose明确使用WORKBENCH_RUN_DISPATCH_MODE=worker。主机旧脚本保留inline兼容模式，此模式仍是进程内执行；不要把它当作队列部署。升级后启动旧主机模式前需显式执行 .venv/Scripts/python.exe -m workbench.cli migrate，再执行原启动脚本。

如手动运行主机队列模式，API与独立进程必须同时使用worker模式及同一数据库/MCP配置。执行进程入口：.venv/Scripts/python.exe -m workbench.run_worker；--once仅领取并处理一个作业用于诊断。只启动API不会自动消费队列。工作台“运行策略”显示实际模式。

## 失败后的处理

| 状态 | 意义与处理 |
| --- | --- |
| queued | 请求已持久化，尚未开始；可以取消，API重启不会把有效排队作业清掉 |
| 正常运行 | 行锁和条件更新控制领取；工具执行时不长期持有网络事务锁 |
| 登录授权失效 | 不调用工具；重新登录，售后可恢复，查单重新发起 |
| worker租约失效 | 已开始的执行状态不确定，不自动重放；售后人工恢复核对，写入仍使用原幂等键 |
| 用户取消 | 后续作业跳过；已经提交的真实业务操作不谎称撤销 |

租约时长为本次活跃执行上限加60秒余量（默认180秒），不是心跳租约。失联执行不会即时被检测；worker恢复轮询后识别过期任务。同步慢SQL不被硬杀，本次不承诺RTO。旧执行失去租约后，检查点和公开结果提交均受校验；不能撤销已经到达业务服务的写入，所以仍需业务幂等与真实状态核对。

## 排错与禁区

先检查七个服务是否运行，重点看run-worker和文档worker是否混淆；再看API就绪、运行状态、公开工具事件及worker错误码。不要为了处理queued就直接改数据库状态或换幂等键重发写入。不要把重启API当作重放作业的手段，不删除数据卷。

测试入口会短暂停止本地验收run-worker并重启API，随后在finally中恢复worker；用于验证真实排队恢复。不要照搬到未授权的生产环境。tests/workbench使用专用测试库并清空测试表，数据库名必须是commercedesk_test前缀，严禁指向业务库。

流程累计工具/token配额与运行准入已补充，详见quotas.md。备份恢复、账号金额预算、队列告警、调度公平性及生产多副本仍有后续工作。无任何服务器购买或上线承诺。
