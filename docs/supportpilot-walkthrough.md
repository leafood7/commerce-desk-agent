# SupportPilot 代码导读：从启动到订单与政策问答

项目根目录：`D:\VScodeProgects\supportpilot-commerce-agent`。
这是一个学习用的订单及政策客服，业务数据独立保存在当前项目。
原项目的物流审批仍未接入本网页。

## 1. 先分清启动过程和每轮请求

运行 `scripts/start-local.ps1`，先初始化订单，必要时建立政策索引，
再启动 FastAPI 与 Streamlit。它不是每次用户提问都重建数据库。

```text
初始化订单 → 初始化政策索引（首次） → 启动 API → 启动网页
```

订单初始化入口：`scripts/init_demo_orders.py`。
它调用 `business/order_repository.py` 的 `initialize_demo_orders()`：
读 `data/demo_orders.json` → 建 orders 表 → INSERT OR IGNORE → 提交。
同一个订单号已存在时不会覆盖。生成的数据库是 `data/supportpilot/orders.sqlite3`。

政策初始化入口：`scripts/init_knowledge.py`。
它调用 `business/knowledge.py` 的 `initialize_knowledge()`：

```text
build_chunks()
  → 读取 data/knowledge/*.txt
  → 第一行作为标题，之后每个非空行作为正文
  → 每个正文片段加上标题、编号、来源
embedding_function()
  → 准备 BGE 模型
collection.upsert()
  → 为片段计算向量，写入 Chroma
  → 清理已删除文档或段落留下的旧编号
```

例：`refund_policy.txt-2` 是片段编号，`refund_policy.txt` 是来源。
向量索引在 `data/supportpilot/chroma`，不在订单的 orders 表里。
模型缓存也在当前项目 `work/huggingface` 中，无需原 SupportPilot 目录。

修改文档后执行 `python scripts/init_knowledge.py`。启动脚本的 `--if-missing`
只负责没有索引时初始化，不自动感知文档改动。

## 2. 一句话怎么从网页到 Agent

默认开启流式输出，实际路径为：

```text
streamlit_app.py 的 main()
  → agent_client.astream(message, thread_id, ...)
client/client.py 的 astream()
  → POST /supportpilot/stream
service/service.py 的 stream()
  → message_generator()
  → _handle_input() 整理消息和会话编号
  → agent.astream(...)
  → LangGraph 执行 model 节点
```

关闭流式时，路径是 `ainvoke()` → `/supportpilot/invoke` → `agent.ainvoke()`。
两种入口最终执行同一张 LangGraph 图。
先理解“网页发送请求，服务执行图”，不必马上研究所有流式事件转换。

## 3. 重点读 supportpilot_agent.py

`TOOLS = [get_order_status, search_knowledge_base]` 是这位 Agent 可以使用的工具。

```python
model = get_model(model_name).bind_tools(TOOLS)
response = await model.ainvoke(
    [SystemMessage(content=INSTRUCTIONS), *state["messages"]], config
)
return {"messages": [response]}
```

- `get_model()` 得到已经配置好的 DeepSeek 兼容客户端。
- `bind_tools()` 把工具名称、描述、参数结构告诉模型。
- `state["messages"]` 是当前会话的消息列表。
- `*` 在这里将列表中的消息逐项放进新列表。
- `SystemMessage` 把行为要求放在消息前面。
- `response` 可能包含最终答案，也可能包含工具调用请求。
- 返回的 messages 被 MessagesState 的合并规则加入已有消息中。

文件底部定义执行顺序：

```python
graph.add_edge(START, "model")
graph.add_conditional_edges("model", tools_condition)
graph.add_edge("tools", "model")
```

```text
START → model
          ├─ response 有 tool_calls → tools → model
          └─ 没有 tool_calls → END
```

`tools_condition` 检查消息是否要求调用工具。`ToolNode(TOOLS)` 真正执行函数，
将返回内容包装成工具消息。不是模型自己执行 Python。
`compile()` 把图组装成可执行对象，`agents/agents.py` 将它注册为 supportpilot。
服务启动时会给图绑定 Checkpointer。

## 4. 跟着 A1002 走一次

用户输入“查询订单 A1002”。模型请求 get_order_status(order_id="A1002")。

```text
ToolNode
  → supportpilot_agent.py：get_order_status()
  → 去空格、转大写、检查格式
  → business/order_repository.py：get_order_from_db()
  → 参数化 SELECT ... WHERE order_id = ?
  → SQLite 返回一行
  → dict(row) 返回订单字典
  → 工具 json.dumps() 返回 JSON 文本
  → model 根据工具结果生成中文回答
```

`mode=ro` 表示查询只读打开订单数据库。
没查到记录返回 None，工具据此说明未找到；数据库文件不存在则提示初始化。
工具不能因此声称执行了退款或创建工单，因为没有提供这些写入工具。

## 5. 跟着“银行卡退款多久到账”走一次

模型应请求 search_knowledge_base(query="银行卡退款到账时间")。

```text
ToolNode
  → supportpilot_agent.py：search_knowledge_base()
  → business/knowledge.py：search_policies()
  → collection.query(query_texts=[query], n_results=3)
  → BGE 将问题转换成向量
  → Chroma 找到最近的候选片段
  → 仅保留 distance <= 0.7 的片段
  → 返回 id、source、content、distance
  → 工具整理成 JSON 文本
  → model 读取资料，回答并标注来源
```

工具返回示意：

```json
{"status":"found","matches":[{"source":"refund_policy.txt","content":"银行卡退款到账时间通常为 3 至 7 个工作日。","distance":0.2}]}
```

结果为空时返回 `status=no_evidence`；索引或模型异常时返回 `status=error`。
两者都不允许模型猜政策。详细错误在服务日志里。

距离不是正确概率；最近片段也可能无关。
例如检索到“黑金会员可以延长退货权益”，仍不能回答“黑金会员年费多少”，
因为资料没有年费金额。提示词明确要求核对资料是否支持所问事实。

## 6. 为什么它能接着上一句话回答

网页每个对话有 `thread_id`。服务把它放进 config 的 configurable，
图通过 Checkpointer 按这个编号读写状态。
当前检查点数据库是 `checkpoints.db`，不是业务订单库或 Chroma。

| 存储 | 内容 |
|---|---|
| data/supportpilot/orders.sqlite3 | 订单业务数据 |
| data/supportpilot/chroma | 政策文本、来源及向量 |
| checkpoints.db | 图的会话检查点与消息 |

SQLite 模式下，跨会话长期 Store 仍是内存实现，不能把它等同于所有记忆均持久化。

## 7. 亲手走一遍

保持启动终端运行，在网页新建对话，分别尝试：

1. 查询订单 A1002：看到 get_order_status 工具与机械键盘信息。
2. 银行卡退款多久到账：看到 search_knowledge_base，回答 3～7 个工作日并引用 refund_policy.txt。
3. 签收后多久能申请电子发票：回答 30 天，引用 invoice_policy.txt。
4. 公司创始人是谁：没有资料，明确无法确认。
5. 黑金会员年费多少：可能检索到会员资料，但仍应说无法确认年费。

在 `work/policy-retrieval.jsonl` 看这一轮 query、候选、距离及 accepted。
在网页展开 Tool Call 可以看工具参数与执行结果。

建议按以下顺序读，每次只追一个值：

1. 订单初始化、get_order_from_db：追踪 A1002 → 订单字典。
2. 两个工具函数：追踪参数 → JSON 文本。
3. call_model 和文件底部的图：追踪 tool_calls → 工具 → 再次调用模型。
4. initialize_knowledge、search_policies：区分建库与检索。
5. client/service：追踪网页消息 → HTTP 请求 → 图执行。

先不要同时阅读所有上游示例 Agent、语音和流式解析细节。
