# WeKnora固定参考与本项目借鉴边界

研究日：2026-11-01。提交：80e8345903ce4af970497de8359b8b4ea4ce4917。仅阅读源码，未部署、未复制代码。

- [阅读工具](https://github.com/Tencent/WeKnora/blob/80e8345903ce4af970497de8359b8b4ea4ce4917/internal/agent/tools/read_document.go)：文档与片段句柄、分页、邻接上下文、读取上限与后续位置。对应阶段1阅读接口、阶段3Agent补读。
- [搜索工具](https://github.com/Tencent/WeKnora/blob/80e8345903ce4af970497de8359b8b4ea4ce4917/internal/agent/tools/search_knowledge.go)：搜索与阅读职责分离。复用现有检索器，不整体替换。
- [结果合并](https://github.com/Tencent/WeKnora/blob/80e8345903ce4af970497de8359b8b4ea4ce4917/internal/application/service/chat_pipeline/merge.go)：父块解析、当前内容合并、FAQ正文、邻接补充与去重，不能将所有扩展当成同一策略。
- [父块共享边界测试](https://github.com/Tencent/WeKnora/blob/80e8345903ce4af970497de8359b8b4ea4ce4917/internal/application/service/chat_pipeline/merge_parent_shared_test.go)：提示父块查询需和授权语义一致；本项目继续禁止跨组织访问，不照搬其共享设计。
- [许可证](https://github.com/Tencent/WeKnora/blob/80e8345903ce4af970497de8359b8b4ea4ce4917/LICENSE)：主体MIT及第三方例外。使用具体模块前核对其依赖许可。

FAQ管理、修订UI、完整追踪尚未源码审查，不把产品介绍当实测结论。后续按新路线逐项补研究与验证。
