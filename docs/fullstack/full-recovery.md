# 完整恢复演练使用说明

本轮对应原路线第4项的恢复验收，不扩展业务模块或容灾平台。当前工程目录为 `D:\VScodeProgects\commerce-desk-agent`。

## 一次新的演练

前提：现有本地验收容器健康，使用隔离演示账号，D盘模型缓存完整，无其他正在执行或排队的业务及文档任务。无需服务器或模型API密钥。

```powershell
cd D:\VScodeProgects\commerce-desk-agent
$env:PYTHONUTF8='1'
$env:TEMP='D:\Docker\temp'
$env:TMP=$env:TEMP
.venv\Scripts\python.exe scripts/verify_full_recovery.py
```

脚本会增加一组带演示标识的订单、政策和等待补充信息的流程。短暂发布rules/guided_v3/clause_bound配置以创建不可变流程快照，随后恢复原已发布配置；若检测到并发配置变化则失败，不覆盖他人版本。

SQL和文档备份在 `work/backups/`；演练状态、独立文件、索引、随机数据库账户配置和私有日志在 `work/recovery/<时间_标识>/`。随机新库名为 `commercedesk_verify_<12位标识>`。数据库用户只授权新库，应用没有原库权限。

恢复API只监听 `127.0.0.1:18092`，与原API18082分开；MCP、执行worker和Chroma使用新Compose项目。复用本地镜像与只读模型缓存，不复制旧向量数据，不下载模型。文档worker无需重新运行：已恢复SQL中的不可变chunk为本次重建依据，原文件哈希仍全部核对。

完成或失败后仅停止新恢复服务以释放内存，保留新库、文件、索引、容器、日志和备份，便于追溯。不会自动删除库、数据卷或切换原应用。

## 中断后的操作

```powershell
.venv\Scripts\python.exe scripts/verify_full_recovery.py --phase resume --directory work/recovery/<原时间_标识>
.venv\Scripts\python.exe scripts/verify_full_recovery.py --phase stop --directory work/recovery/<原时间_标识>
```

已成功完成的演练继续命令只提示已验证，不重新启动或改写结果。失败中的继续仅复用已写出的阶段检查点，并非保证能从任意指令中间恢复：未完成的索引若已有数据，重建脚本会拒绝写入。此时保留失败目录，查明原因后发起新的演练，不清空旧索引冒充空目标。`18092`被占用时同样停止检查，不抢占现有服务。

## 实测验收

结果见 [报告](full-recovery-report.md)、[检查清单](recovery-checks.json) 和 [原始公开证据](recovery-evaluation/result.json)。本轮34张表恢复校验一致、62份文件哈希一致；30个当前生效的已发布版本各有1个SQL片段，实际重建30片段。不是把先前长文规模题集全部导入此次运行库。

重建前索引必须没有任何collection，且配置库名必须与指定验证库一致、索引必须指向恢复服务。只有当前已发布且生效版本参与重建；组织、版本、引用和撤回的最终权威仍是SQL。

可验证链路：恢复原暂停ID → 真实MCP检索 → 补充商品状态 → 预览确认 → 独立审批 → 模拟受理 → 重启恢复服务 → 读取同一受理结果。重复继续键没有新增申请或重复受理。撤回只发生在恢复副本，原环境保持原状态。

## 边界

这是同一台Windows主机、同一个Docker和MySQL引擎、同版本代码、正常暂停点的恢复演练。没有验证异机恢复、跨版本迁移、运行中写操作崩溃、生产切换、真实退款或生产RTO/RPO。备份含账号哈希和会话，保持私有；不要上传整个work目录。
