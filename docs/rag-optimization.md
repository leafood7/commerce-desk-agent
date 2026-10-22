# RAG 检索

知识库包含18份政策、76个片段。文件首行为标题，后续每个非空行作为片段，保留来源文件与片段编号。

```text
问题 → BGE向量召回＋BM25召回 → RRF融合去重
→ BGE交叉编码器重排 → 阈值过滤 → Top3锚点 → 同源条款补全 → 回答并引用来源
```

- 向量模型：`BAAI/bge-small-zh-v1.5`，存储使用 Chroma。
- 重排模型：`BAAI/bge-reranker-base`，代码固定模型版本。
- 候选池12，重排阈值-2；保留 `vector`、`hybrid`、`hybrid_rerank` 三策略用于对照。

配置通过 `.env` 的 `SUPPORTPILOT_RETRIEVAL_STRATEGY`、`SUPPORTPILOT_RAG_POOL_SIZE`、`SUPPORTPILOT_RERANK_THRESHOLD` 设置。

检索返回相关片段不等于问题可回答。工具区分 `found`、`no_evidence`、`error`；模型需要核对证据，缺少依据时明确无法确认。

多轮对话除auto完整匹配的独立问候、致谢和明确纯查单外，先检查已有证据。检查结果不足则重新查询；足够则复用真实片段。复用ID由程序验证，语义覆盖仍依赖模型判断。

对比结果和运行命令见 [评测说明](evaluation.md)。三个已知漏检已完成阶段诊断和同源条款补全，固定查询必要证据均补齐；在线验证中真实三条流程的主要结论正确，但固定单轮食品券仍过度拒答，部分工具能力承诺仍越界。详见 [在线验证](rag-miss-online-validation.md)。原因、前后对比与代价见 [漏检修复报告](rag-miss-fix.md)。

补全保留真实条款ID和来源，按同源邻段距离与来源轮流分配正文预算1800字符；原锚点不裁剪，预算不含JSON和历史开销。不新增模型调用，但会增加回答输入长度。

最新回答约束、成本对照与保留失败见 [回答质量与检查成本](answer-quality-results.md)。同源补证不保证回答始终正确。
