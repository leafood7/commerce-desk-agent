# RAG 检索

知识库包含18份政策、76个片段。文件首行为标题，后续每个非空行作为片段，保留来源文件与片段编号。

```text
问题 → BGE向量召回＋BM25召回 → RRF融合去重
→ BGE交叉编码器重排 → 阈值过滤 → Top3 → 回答并引用来源
```

- 向量模型：`BAAI/bge-small-zh-v1.5`，存储使用 Chroma。
- 重排模型：`BAAI/bge-reranker-base`，代码固定模型版本。
- 候选池12，重排阈值-2；保留 `vector`、`hybrid`、`hybrid_rerank` 三策略用于对照。

配置通过 `.env` 的 `SUPPORTPILOT_RETRIEVAL_STRATEGY`、`SUPPORTPILOT_RAG_POOL_SIZE`、`SUPPORTPILOT_RERANK_THRESHOLD` 设置。

检索返回相关片段不等于问题可回答。工具区分 `found`、`no_evidence`、`error`；模型需要核对证据，缺少依据时明确无法确认。

多轮对话每轮先检查已有证据。检查结果不足则重新查询；足够则复用真实片段。复用ID由程序验证，语义覆盖仍依赖模型判断。

对比结果和运行命令见 [评测说明](evaluation.md)。手机开箱、金卡权益、优惠券跨品类等问题仍有漏检，需继续排查召回与重排。
