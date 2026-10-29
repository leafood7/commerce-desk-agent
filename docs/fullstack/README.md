# CommerceDesk 全栈运行与模块说明

当前工程：`D:\VScodeProgects\commerce-desk-agent`。新React工作台是主交付入口；[作品交付包](../portfolio/README.md)、[实际架构](../portfolio/architecture.md)、[启动排障](../portfolio/operations.md)和[当前状态](STATUS.md)优先于旧阶段文字。

## 当前本地候选（2026-10-29）

已贯通上传发布与RAG、HTTP MCP、订单行级退货、条件更正、独立质检、审批及模拟受理、SSE、会话/配置快照和持久任务。最新0009迁移、34表。默认规则与neighbors；受限模型和引用扩展保留实验开关，没有真实退款或Linux线上部署。

本地Docker推荐页面http://127.0.0.1:15173，API http://127.0.0.1:18082。7服务包括MySQL、API、Chroma、MCP、文档worker、运行worker和Web。恢复演练已完成，见[操作](full-recovery.md)和[报告](full-recovery-report.md)。三段作品演示及证据见[交付包](../portfolio/README.md)。

作品交付分支为`feat/fullstack-foundation`，以实际Git提交为准；历史候选清单与“未提交”记录描述其运行时状态。以下原生启动为兼容路径，使用另一套端口/私有凭据；不要与容器演示清单混用。更早分阶段记录保留在STATUS及各报告，不能将历史待办当作最新状态。

## 启动本机版本

项目已配置仅监听本机的 MySQL 8.4.13 便携实例，端口 33316，不安装 Windows 服务。开发数据、日志和随机凭据位于被 Git 忽略的 `work/fullstack/`。

```powershell
cd D:\VScodeProgects\commerce-desk-agent
uv tool run --from uv==0.12.5 uv sync --frozen --extra supportpilot
npm --prefix frontend ci --ignore-scripts

# 首次配置；会迁移数据库，创建两个演示商家和三种角色，不重置已有密码。
.venv\Scripts\python.exe scripts/setup_workbench.py
powershell -ExecutionPolicy Bypass -File scripts/start-workbench.ps1
```

工作台：<http://127.0.0.1:5173>；API 文档：<http://127.0.0.1:8082/docs>；就绪检查：<http://127.0.0.1:8082/ready>。

演示商家为 `demo-a`、`demo-b`；账号分别为 `admin@demo.test`、`support@demo.test`、`approver@demo.test`。密码保存在本机的 `work/fullstack/local-login.json`，不写入文档或提交。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/stop-workbench.ps1
```

停止脚本核对进程归属后停止 API/前端进程树；不停止共享数据库。首次迁移已准备好便携 MySQL；换一台机器需要自己提供 MySQL 或使用下面的 Compose，不会自动下载/安装系统软件。

## 独立数据库操作

```powershell
$env:PYTHONPATH='src'
.venv\Scripts\python.exe -m workbench.cli migrate
.venv\Scripts\python.exe -m workbench.cli check
$env:WORKBENCH_DEMO_PASSWORD='<至少12字符的本机演示密码>'
.venv\Scripts\python.exe -m workbench.cli seed
```

`WORKBENCH_DATABASE_URL` 是独立的业务数据库配置，不复用 LangGraph 的 `DATABASE_TYPE`。开发/测试可以显式使用 SQLite；生产模式要求 MySQL，不会连接失败后静默退回 SQLite。

生产账号初始化、HTTPS、数据库最小权限与备份恢复仍属于后续部署验收。演示种子命令在 production 模式被拒绝。

## 身份与权限

- 会话采用随机、不透明 Bearer token；数据库只保存 token 哈希，支持超时、退出撤销和账号停用。
- 密码使用带随机盐的标准库 scrypt；失败登录有持久化的账号/IP 频率限制。客户端输入和日志不回显密码。
- 组织和用户取自服务端认证结果；请求体不允许传 user_id、organization_id 或审批人冒认身份。
- 客服可以创建催单申请；审批员可以审批；管理员创建/停用本组织账号。会话、运行和事件仅对创建者公开，组织内其他员工也不能读取私人会话。
- 新应用没有 legacy 的 /invoke、/history、无角色审批入口，避免绕过新权限。保留的 legacy 服务只供本机比较，不提供多用户隔离承诺。

## API

| 入口 | 用途 |
| --- | --- |
| POST /api/v1/auth/login；GET /auth/me；POST /auth/logout | 登录、当前身份与退出 |
| POST /api/v1/users；POST /users/{id}/disable | 管理本组织账号 |
| GET /api/v1/orders；GET /orders/{number}；GET /orders/{number}/tickets | 订单、明细与工单历史 |
| POST /api/v1/conversations；GET /conversations；GET /conversations/{id}/messages | 私人会话目录与公开历史 |
| POST /api/v1/runs；GET /runs/{id}；POST /runs/{id}/cancel | 持久查单运行与取消 |
| GET /api/v1/runs/{id}/events | 带事件 ID 的 SSE，支持 Last-Event-ID/after 续订 |
| POST/GET /api/v1/shipping/requests | 创建与查看催单申请 |
| POST /api/v1/shipping/requests/{id}/decision | 有角色校验的加急审批 |

表中省略的同组路径均以 /api/v1 为前缀。运行创建与催单创建支持 Idempotency-Key；同键不同输入返回冲突。运行支持order_lookup、knowledge_search、aftersales；默认规则模式不调用付费模型。

订单预计信息与实际签收分别保存和展示；未知签收时间不由 ETA 推算。演示事实标记为 demo_seed。金额以整数分保存。

## 数据与并发

业务表统一使用 wb_ 前缀，Alembic 初始版本为 0001_workbench；迁移可升级、回退并检查模型漂移。checkpoint 保留现有实现，不把业务写入与 checkpoint 伪装成跨库事务。

MySQL 中创建和审批都先锁订单，再锁申请；事务、唯一约束和幂等键控制重复工单。并发测试实际发现过“请求已完成但响应暂缺工单号”的一致性问题：认证读取建立的旧快照与后续锁定读取混用。现已结束旧事务后渲染一致事实，重复审批返回同一个工单。

列表查询按过滤/排序设计组合索引；订单列表批量取商品，避免每条订单再查询一次商品。索引实验原始结果见 [index-benchmark.json](index-benchmark.json)：一万条合成订单、50个组织，比较三类查询的 EXPLAIN 与5次热耗时。IGNORE INDEX 只排除目标索引，不等于移除全部索引；工单查询可能继续选择已有更合适的索引，不包装成全部变快。

主机inline兼容模式仍采用进程内后台任务。Compose使用MySQL持久队列与独立run-worker，排队作业可跨API重启；已开始且租约失效的执行只标记interrupted，不自动重放写入。检查点及公开发布受租约校验，仍不能宣称已经验证生产多副本高可用。

## 验证

```powershell
# SQLite 基础验证
.venv\Scripts\python.exe -m pytest tests/workbench -q

# 专用真实MySQL测试，测试会清空该测试库，不得指向业务库。
$env:WORKBENCH_TEST_MYSQL_URL='mysql+pymysql://.../commercedesk_test?charset=utf8mb4'
.venv\Scripts\python.exe -m pytest tests/workbench -q

# 专用索引库，脚本拒绝在非 commercedesk_index_* 数据库生成数据。
$env:WORKBENCH_BENCHMARK_DATABASE_URL='mysql+pymysql://.../commercedesk_index_test?charset=utf8mb4'
.venv\Scripts\python.exe scripts/benchmark_workbench_indexes.py

# 运行中工作台的真实浏览器验证；凭据从本机忽略文件或环境变量读取。
npm --prefix frontend exec -- playwright install chromium
npm --prefix frontend run test:e2e
npm --prefix frontend run build
```

真实 MySQL 测试覆盖并发审批和同键并发运行，SQLite 不模拟行锁，这两项在 SQLite 上明确跳过。浏览器测试覆盖登录/查单/退出、组织隔离和事件订阅失败后续订；不启用包含认证请求的网络 trace。

原有客服、证据隔离、MCP、服务与 Streamlit 测试继续保留。未完成的 export-review/promotion 实验命令明确拒绝执行；新补的实验测试只校验开发题和预算机制，没有揭盲最终题或运行付费批次。

## Docker

默认 compose.yaml 包含MySQL、API、Chroma、MCP、文档worker、run-worker和React/Nginx；数据库不公开宿主端口，工作台只绑定本机。服务镜像的 workbench target 使用独立依赖组，基础 API 不打包 GPU/检索模型。legacy target 包含原有业务、MCP、政策与检索依赖。

设置本机 .env 中的 WORKBENCH_MYSQL_PASSWORD、WORKBENCH_MYSQL_ROOT_PASSWORD，以及 URL 编码后的 WORKBENCH_DOCKER_DATABASE_URL：

```text
mysql+pymysql://commerce:<URL编码后的密码>@mysql:3306/commercedesk?charset=utf8mb4
```

```powershell
docker compose up --build -d --wait
docker compose exec -e WORKBENCH_DEMO_PASSWORD='<本机演示密码>' api python -m workbench.cli seed
# 日志
docker compose logs --tail=100 api
```

生产部署、HTTPS和恢复演练尚未完成。原演示使用 docker compose -f compose.legacy.yaml；首次需下载/预热检索模型，应单独评估该镜像体积和启动资源。

CI 已增加真实 MySQL、前端构建和可手动触发的 Docker 完整链路任务。工作流代码的存在不表示远程 CI 已执行；本轮没有提交或推送。最终执行记录见 [implementation-status.md](implementation-status.md)。


## MCP 与知识库后续批次

已接入专用授权 MCP、文档版本/发布/撤回和真实向量检索。本地启动脚本现统一管理六类进程，Compose也补充知识组件，但Docker实跑继续延期。具体运行、实验与边界见 [knowledge.md](knowledge.md)，本批次独立验收见 knowledge-checks.json。上文首批 Docker 三服务描述为历史起点，当前配置以 compose.yaml 为准。


## LangGraph 售后闭环

新增“售后助手”和独立“加急审批”。真实 StateGraph 与 MySQL 检查点支持补充、更正、确认、等待审批及恢复，运行在已有 MCP 和业务服务之上。详情、边界及复现见 [aftersales.md](aftersales.md)，本批次验收见 aftersales-checks.json。默认使用规则意图路由，无付费模型调用；可选单次模型分类不负责业务授权。
# 最新业务深化

第11次精进补充长文档和12／60／180份合成资料的规模RAG验证、有界跨条款引用，以及管理员长文来源分页。768条实验记录和作者参与复核分开保留；引用模式默认关闭。见 [使用说明](rag-long-scale.md)、[阶段报告](rag-long-scale-report.md)、[验收记录](rag-long-scale-checks.json) 和 [完整对照与匿名证据](rag-long-scale-evaluation/report.md)。

第10次精进已完成显式质量问题退货分支、独立缺陷判定与同文档条件／例外证据投影。见 [操作说明](quality-return.md)、[阶段报告](quality-return-report.md)、[验收记录](quality-return-checks.json)。迁移0009仍34表，未增加付费调用；RAG投影与补检结果分开报告。

第9次精进已完成演示质检工单、只读 MCP 查询及退货 Agent 等待刷新闭环。见 [操作说明](quality-inspections.md)、[阶段报告](quality-implementation-report.md)、[验收记录](quality-checks.json)。数据库为0008、34表，运行时保持规则模式。

第8次精进已完成原话候选确认、事实边界核对和唯一动作免模型调用。见 [操作说明](return-intake.md)、[阶段报告](return-intake-report.md)、[验收记录](return-intake-checks.json)。本轮没有新增付费请求。
