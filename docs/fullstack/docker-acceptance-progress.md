---
date: 2026-10-28
project: commerce-desk-agent
status: 环境安装完成，等待重启，容器验收未完成
---

# 2026-10-28｜Docker环境安装与验收准备

## 1. 做了什么、补充了什么

用户授权Docker实机验收，并要求镜像、数据卷与缓存优先D盘。

本阶段已检查硬件、安装官方WSL与Docker、启用虚拟机平台，补充独立Compose验收配置、分阶段执行脚本及容器业务测试。**目前尚未构建镜像、启动六服务或完成容器业务和重启验收。**

Windows明确返回需要重启；当前虚拟化后端尚未生效。没有自动重启电脑，等待用户保存工作并重启后继续。

## 2. 实现的功能、意义与价值

- Docker程序与大容量数据目标分开配置，主要放D盘。
- 专用验收项目、端口及数据卷，避免误连本机开发环境或污染原订单。
- 将配置、构建、启动、功能与恢复验收分阶段，避免“配置存在就算部署成功”。
- Linux检索依赖使用CPU版PyTorch，减少不使用的GPU依赖。
- 为后续真实验收提供可执行入口和可追溯状态，当前价值属于工程准备，不是线上业务收益。

## 3. 安装与存储位置

| 内容 | 位置或结果 |
|---|---|
| 项目代码 | D:\VScodeProgects\commerce-desk-agent |
| Docker Desktop4.96.3 | D:\Docker\app，程序文件逻辑总量约3.33GiB |
| 镜像、容器与卷的数据盘目标 | D:\Docker\data |
| 安装日志和校验 | D:\Docker\logs、D:\Docker\setup |
| 工具缓存、临时目录 | D:\Docker\cache、D:\Docker\temp |
| 备份预留 | D:\Docker\backups\commerce-desk-agent |
| 已有模型 | 项目work\huggingface\hub，后续只读挂载 |
| WSL系统运行时 | C:\Program Files\WSL，约1.14GiB |

已读取安装设置，确认wslDefaultDataRoot为D盘。**实际数据VHDX尚未生成/核对，不能称为镜像和数据卷已在D盘实跑。**

Windows组件和系统安装缓存仍占C盘。检查期间C盘空闲约75.54GB降至73.49GB，这是整个系统期间的差额，不能精确全部归因于本次安装。没有删除其他软件、WindowsInstaller缓存或系统组件。

## 4. 针对解决的问题

| 问题 | 措施与已验证程度 |
|---|---|
| 没有容器运行环境 | 官方WSL3.0.3与Docker安装成功，虚拟机平台启用；引擎待重启 |
| 默认C盘存储 | 官方安装参数指定D盘，安装设置核对；实际VHDX待实测 |
| 下载提前结束 | 大小校验拦截，HTTP Range续传，哈希和签名验证后才安装 |
| Linux默认带GPU包 | 显式CPU来源和知识依赖，重新锁定并检查包列表 |
| 本机与容器验收混用 | 独立项目commercedesk-acceptance、API18082、Web15173 |
| 审批浏览器地址写死 | 改为尊重WORKBENCH_WEB_URL，避免误连本机5173 |
| 密码泄露 | 验收环境保存在忽略的私密文件，Compose只执行quiet/services |

## 5. 产出与效果：区分实际通过和未执行

| 项目 | 结果 |
|---|---|
| 硬件 | Windows11专业版；15.8GB内存；BIOS虚拟化已开 |
| WSL | 3.0.3.0，版本命令正常 |
| Docker Desktop／CLI／Compose | 4.96.3／29.9.2／v5.6.1，安装退出码0 |
| VirtualMachinePlatform | 原Disabled，启用后返回必须重启 |
| 安装包 | WSL官方SHA256匹配；Microsoft与Docker数字签名均Valid |
| 六服务配置 | 真实Compose解析通过 |
| 验收脚本 | PowerShell语法和Config阶段通过 |
| 容器API测试 | 3条成功收集，**没有执行** |
| Linux依赖 | 移除19个GPU相关包，锁记录284→266，Linux为torch2.14.1+cpu |
| 静态检查 | 锁文件、Ruff、前端TypeScript/Vite、补丁格式通过 |
| 安装包清理 | 删除两个完成安装的原始包，回收约962.6MB |
| 真实付费模型 | 0次调用 |

尚未测量镜像体积、构建耗时、容器功能和恢复效果，不能编造节省具体GB数、部署成功率或业务KPI。本轮未提交、推送，也未读取封存最终题。

## 6. 技术与作用

- Docker Desktop、WSL2、VirtualMachinePlatform：运行Linux容器的基础。
- PowerShell管理员进程：启用组件、安装、保存状态；不自动重启。
- SHA256、Authenticode、HTTP206/Content-Range：完整性、发布者验证和分段续传。
- Compose覆盖配置：独立端口、卷、环境和资源上限。
- uv显式索引与平台条件：Linux选择官方CPU版PyTorch，Windows保持原来源。
- pytest、HTTPX、Playwright：准备API与真实浏览器验收。
- 私密环境文件：隔离演示密码，不写进代码或公开报告。

## 7. 实际遇到的困难及处理

**下载不完整。** 首次两个下载未达官方大小。没有运行不完整安装包；确认官方支持范围请求，分段补齐后再验证大小、WSL哈希和数字签名。

**授权与管理员权限不同。** 当前工作进程不是提升的管理员进程，Windows组件操作要求系统授权。通过明确的管理员安装脚本完成，没有绕过UAC。

**安装不等于可运行。** 启用虚拟机平台返回RestartNeeded；不能在未重启时宣称引擎成功。记录断点，等待重启。

**仅配索引没有约束传递依赖。** 第一次添加CPU映射后torch仍来自PyPI。把现用版本显式声明为知识服务依赖后重新锁定，检查CPU来源及GPU包确实移除。

## 8. 如果我独立做，可能遇到什么困难

以下是预期学习难点，与实际故障分开记录：

1. 混淆CLI已安装和引擎可用：分开检查版本、引擎、磁盘、容器。
2. 容器localhost指错服务：理解服务名DNS与内部端口，不照抄本机连接。
3. 依赖在不同平台体积不同：检查目标平台锁文件，别只看Windows运行正常。
4. 验收误用旧服务：使用独立项目、账号、卷和端口。
5. 只看HTTP200：还要核实来源、撤回、审批角色、实际工单和重启后的状态。
6. C盘仍增长：区分系统运行时、安装缓存、镜像数据盘、交换文件及工具缓存。

## 9. 产出文件

项目内：
- compose.acceptance.yaml。
- scripts/run-container-acceptance.ps1。
- tests/integration/test_workbench_docker_flows.py。
- frontend/e2e/aftersales.spec.ts的可配置地址修正。
- pyproject.toml、uv.lock的Linux CPU依赖。
- docs/fullstack/DOCKER-RESUME.md与docker-acceptance-checks.json。
- work/docker/acceptance.env为私密文件，禁止输出或提交。

D盘安装记录：D:\Docker\setup\installation-status.json、download-verification.json、signatures.json、installed-footprint.json。

## 10. 接下来做什么

1. 保存工作并重启Windows，回来后说“继续Docker验收”。
2. 先读DOCKER-RESUME.md；**不要重复下载安装**。
3. 检查虚拟化生效，启动D盘Docker Desktop。
4. 验证实际VHDX位于D盘，核对WSL资源与交换文件，避免C盘大量增长。
5. 执行脚本Build、Start、Test，记录真实结果。
6. 另做容器重启、数据卷、图暂停恢复和故障排查，Test阶段通过不能代替这些检查。
7. 清理无用测试临时文件，更新本总结。

## 11. 当前结论

环境安装与验收准备完成；**完整Docker实机验收未完成，必须在Windows重启后继续**。

参考：[Docker安装参数](https://docs.docker.com/desktop/setup/install/windows-install/)、[WSL与重启要求](https://learn.microsoft.com/en-us/windows/wsl/install)、[uv CPU依赖配置](https://docs.astral.sh/uv/guides/integration/pytorch/)。
