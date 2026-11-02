# 可切换的向量存储

## 已实现的功能

入库、MCP检索与索引维护使用统一接口；可通过服务端配置选择Chroma或Qdrant。默认仍是原Chroma和原集合名，不搬动原数据。

知识库管理员可在页面看到当前存储配置。执行追踪可展开`chroma.query`或`qdrant.query`等实际步骤。客户端和模型不能选择后端、地址或密钥。

## 一条完整的切换链

1. 停止新增发布和入库，等待现有任务结束，确认原文件、业务库和本地模型缓存都可访问。
2. 配置候选Qdrant地址，使用全新的`rebuild_...`命名空间重建。
3. 命令只读取MySQL中当前有效的已发布版本，校验原文件、片段数量、Embedding版本，逐批写入后读回核对。
4. 写入前后再次检查来源清单；期间政策变化时拒绝给出可切换结果。成功后输出维护清单，不自动修改运行配置。
5. 人工将入库服务和MCP服务等相关进程切到同一后端与命名空间，重启后确认来源关联，再恢复写入。
6. 若切换期间已有新知识写入，回到Chroma前先从当前SQL片段重建新的`restore_...`命名空间，不直接复用已经过期的旧索引。

现有索引不删除、不覆盖，没有自动双写或自动故障切换。候选服务不可用时返回错误；缺少应存在的集合时明确提示索引未就绪，不偷偷创建空集合掩盖配置问题。

## 维护命令

在装有知识服务依赖、且正确配置了业务库、原文件目录、本地模型缓存与向量服务地址的运行环境中执行。容器中的输出目录应有持久挂载，不能在删除临时容器时丢掉维护清单。

```text
python -m workbench.vector_admin status
python -m workbench.vector_admin rebuild --backend qdrant --target-namespace rebuild_example --expected-database commercedesk --output work/vector-rebuild.json
python -m workbench.vector_admin rebuild --backend chroma --target-namespace restore_example --expected-database commercedesk --output work/vector-return.json
```

命令要求确认实际源库名；SQLite开发环境确认文件名。目标必须是新的隔离命名空间，非空目标会被拒绝，已有输出清单不会覆盖。默认片段上限50000，可用`--max-chunks`明确调整，最大100000；这只是保护边界，不是规模能力结论。

`status`只说明配置与命名空间是否存在，不代表来源全部完整，也不代替重建核对。切换期间应保持停写，避免重建完成后又产生新发布。

## 服务配置

- `WORKBENCH_VECTOR_BACKEND=chroma|qdrant`
- `WORKBENCH_VECTOR_NAMESPACE=`：原Chroma默认空值，保留历史集合名。
- `WORKBENCH_QDRANT_URL=http://127.0.0.1:6333`
- `WORKBENCH_QDRANT_API_KEY=`：可选服务端密钥，维护接口不返回它。
- `WORKBENCH_VECTOR_TIMEOUT_SECONDS=30`：Qdrant HTTP请求超时；整个工具调用另受MCP预算限制。

可选部署文件`compose.qdrant.yaml`与原`compose.yaml`叠加使用，需显式设置已重建的命名空间。Qdrant固定为`qdrant/qdrant:v1.13.6`，默认不向宿主机暴露端口。默认数据目录为项目下`data/workbench/qdrant`，本机项目在D盘；可配置`WORKBENCH_QDRANT_DATA=D:/Docker/data/commerce-desk-agent/qdrant`。连接外部服务时，服务端与客户端需各自正确配置认证；客户端支持密钥不等于自动开启服务端认证。

## 保留的业务边界

MySQL仍负责商家、有效版本、来源正文、FAQ依赖和政策范围判断。向量库只返回候选定位与距离，之后仍经过SQL来源核对及发布状态复查。更换存储不改变审批、金额或业务写入权限。

两种实现沿用相同固定版本BGE。Chroma使用余弦距离；Qdrant余弦相似度转换为`1-score`后进入原距离过滤。统一了数值含义，不承诺两个后端排序完全相同。Qdrant只保存向量、定位及内容哈希，答案原文从SQL读取。

接口位于`src/workbench/vector_store.py`，Qdrant实现位于`qdrant_index.py`，原Chroma实现在`knowledge_index.py`。旧`rebuild_recovery_index.py`继续只允许原Chroma隔离恢复；新维护命令支持两种后端，不替代业务库和原文件备份。

## 本轮实际操作范围

使用新建隔离库和人工演示政策，实际跑通Chroma入库、Qdrant重建、LangGraph规则流程经HTTP MCP查询、商家与版本过滤、Qdrant重启后续查、新版政策入库，再重建回Chroma。浏览器确认配置与真实向量调用步骤可见。没有使用真实客户或日常业务库，没有调用付费模型。

这次交付可用的适配与维护功能，没有开展召回质量、吞吐、延迟或成本优劣对照；不据此推荐永久更换默认后端。

协议参考：[Qdrant查询接口](https://api.qdrant.tech/v-1-13-x/api-reference/search/query-points)、[集合配置](https://api.qdrant.tech/v-1-13-x/api-reference/collections/create-collection)、[批量写入](https://api.qdrant.tech/api-reference/points/upsert-points)。
