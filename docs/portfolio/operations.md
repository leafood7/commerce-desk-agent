# 本机启动与排障

本说明面向当前已配置好的Windows机器。新机器仍需准备Docker、依赖、模型缓存和私有环境文件；不宣称任意机器下载后零配置启动。原生启动的5173/8082与容器的15173/18082不能混用账号、数据和演示清单。

## 推荐演示路径

```powershell
cd D:\VScodeProgects\commerce-desk-agent
powershell -ExecutionPolicy Bypass -File scripts/run-container-acceptance.ps1 -Phase Config
powershell -ExecutionPolicy Bypass -File scripts/run-container-acceptance.ps1 -Phase Start
```

已运行且健康时不用重复Start；代码未变时不用重建镜像。确有源代码变化需要重建时先运行同一脚本的 `-Phase Build`，再Start；该阶段可能读取本地代理，准备足够D盘空间。它不应成为演示前的例行动作。

- 客户端：http://127.0.0.1:15173。
- API：http://127.0.0.1:18082/docs；就绪：http://127.0.0.1:18082/ready。
- 7服务：mysql、api、chroma、mcp、worker（文档）、run-worker（业务运行）、web。
- Docker安装/配置/镜像数据盘在 `D:\Docker\`；模型缓存在项目 `work/huggingface/hub`；私有容器配置 `work/docker/acceptance.env`，不要公开。
- 容器演示账号demo-a：support@demo.test、approver@demo.test、admin@demo.test。密码是私有配置中的WORKBENCH_DOCKER_DEMO_PASSWORD；原生启动的local-login.json可能是另一套密码。

准备新订单和政策、打印新的文件位置：

```powershell
$env:PYTHONUTF8='1'
$env:TEMP='D:\Docker\temp'
$env:TMP=$env:TEMP
.venv\Scripts\python.exe scripts/run_portfolio_demo.py --prepare-only
```

自动重放三段已有浏览器场景，去掉 `--prepare-only`。它会新增演示数据、在本次私有目录保留旧演示指针、更新work/returns-v1/demo.json；不会删除或修改旧订单。浏览器专项会按需临时发布规则配置并恢复，演示期间不要并发修改配置。不要和整套容器验收同时运行。

若需要重新完整验收，用 `scripts/run-container-acceptance.ps1 -Phase Test`；它会配套准备API与浏览器演示清单，不能只把任意主机清单拿来跑容器浏览器测试。

## 日志与状态（只读）

```powershell
$env:DOCKER_CONFIG='D:\Docker\config'
& 'D:\Docker\app\resources\bin\docker.exe' --host npipe:////./pipe/dockerDesktopLinuxEngine compose --project-name commercedesk-acceptance --env-file work/docker/acceptance.env -f compose.yaml -f compose.acceptance.yaml ps
& 'D:\Docker\app\resources\bin\docker.exe' --host npipe:////./pipe/dockerDesktopLinuxEngine compose --project-name commercedesk-acceptance --env-file work/docker/acceptance.env -f compose.yaml -f compose.acceptance.yaml logs --tail 80 api mcp run-worker worker
```

不要把展开后的Compose配置、完整环境变量、认证网络trace或私有SQL贴进公开材料。正常结束截图也须逐张检查；工具内部错误日志留在work排查。

## 常见故障

|现象|先核对什么|处理原则|
|---|---|---|
|Docker命令存在但连接不到引擎|Desktop Linux引擎、虚拟化、D盘数据盘|先检查已有安装，不反复重装|
|页面可开，订单不存在|15173/18082与本轮demo.json是否同环境|准备一组新的演示清单，不伪造订单事实|
|登录失败|商家、角色、容器密码与原生密码差异|不要反复猜测触发频率限制；不输出密码|
|上传后一直排队|worker进程、SQL文档作业状态、原文件挂载|文档索引worker与业务run-worker是两个职责|
|MCP工具超时|mcp健康、模型首次加载、授权地址与scope|看实际错误；只读有限重试，写结果不确定先查状态|
|检索空或错误分支|文档是否发布/生效、版本范围、实际候选与重排、当前分支|不要先增加top_k或把业务映射当召回结果|
|看见政策但不能办理|缺少实际签收、商品状态或独立缺陷结论，规则是否适用|政策不是事实，不能为演示绕过条件|
|改条件后不能确认|原预览与新输入不一致|重新核对并确认；这是预期保护|
|审批后客服页面仍等待|刷新审批结果、当前暂停ID|不由客服自批准；不用旧暂停ID反复提交|
|演示申请数量被占用|同商品已经有待办或已受理申请|prepare-only创建新演示订单，不清空业务表|
|刷新后列表状态与详情不同步|重新加载列表、以当前流程详情及业务状态为准|存在列表显示的刷新局限，不外推为实时全局一致|

## 退出与恢复

需要释放本机资源时使用 `scripts/run-container-acceptance.ps1 -Phase Stop`；仅停止容器，保留数据。不要使用删除数据卷或覆盖库的操作。

完整恢复复演见 [full-recovery.md](../fullstack/full-recovery.md)。它新增独立库和索引，正常结束后停止恢复服务。上一轮验证过的恢复目录保留为证据；不要把“继续已完成演练”当作新恢复实测。

主机兼容启动和依赖初始化见 [全栈说明](../fullstack/README.md)，原Streamlit入口见 [旧版运行说明](../../LOCAL_SUPPORTPILOT.md)。Linux服务器、HTTPS和生产运维仍未验收。

本地候选清单通过 `scripts/verify_portfolio_delivery.py` 生成，它只读核对容器/浏览器与材料，再更新交付证明。代码、构建或材料变化后需要重新核对；文件哈希不是Git发布标签，也不是可独立安装的源码压缩包。
