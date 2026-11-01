# 第15次精进：真实政策语义诊断与一万片段容量验收

日期：2026-10-29；工程：`D:/VScodeProgects/commerce-desk-agent`。按用户要求先完成本轮，再做源码走读。没有改动生产回答架构、默认模型策略或审批规则。本轮是验证能力升级，不是上线准确率提升承诺。

## 实际交付与证据

- [冻结协议](rag-real-scale-protocol.md)、[5份来源摘要与24题](../../data/evaluation/rag_real_v1/manifest.json)：12开发/12复核，家族不跨组。未读取旧封存最终题。
- [原始检索轨迹](rag-real-scale-evaluation/real-traces.jsonl)：24题×3配置=72条，通过真实上传API、MySQL入库、生产解析与worker、HTTP Chroma、生产检索器运行。上传API使用TestClient，不是新浏览器全链路。
- [48份真实模型回答](rag-real-scale-evaluation/model-answers.jsonl)、[匿名材料](rag-real-scale-evaluation/model-anonymous-review.json)、[语义复核](rag-real-scale-evaluation/semantic-review.json)：相同问题/生成设置比较全局与指定版本证据，交替运行。匿名材料有映射；编码助手已见过版本标记，不能称独立盲审。
- [容量档位原始数据](rag-real-scale-evaluation/capacity-tiers.json)、[复算指标](rag-real-scale-evaluation/capacity-reviewed-metrics.json)、[权限与发布检查](rag-real-scale-evaluation/capacity-guards.json)、[验收清单](rag-real-scale-checks.json)。

## 来源与适用范围

|来源|本轮涉及差异|
|---|---|
|[Apple大陆线上帮助](https://www.apple.com.cn/shop/help/returns_refund)|交付起算、取货店渠道、退货处理与实际银行入账区分|
|[Apple大陆零售政策](https://www.apple.com/legal/sales-support/sales-policies/retail_cn.html)|购买起算、原店、商品例外、软件条款特例|
|[宜家大陆售后](https://www.ikea.cn/cn/zh/customer-service/returns-claims/)|普通与会员期限、货品凭证、已抵扣发票红字流程|
|[宜家台湾政策](https://www.ikea.com.tw/zh/customerservice/about-shopping/return-policy)|会员拆封与退款卡、瑕疵例外、床垫范围|
|[Decathlon美国政策](https://www.decathlon.com/policies/refund-policy)|地区限制、普通与高端车分支、退运和费用上限|

语料是作者整理、注明官方URL的有限中文摘要，非完整政策原文，非自动抓取解析效果验证；摘要文件冻结哈希可复现，网页未保存整页快照，今后可能变化。核查日不等于政策生效日，未显示生效日一律未知。小米候选页只提取到导航、优衣库超时，未计入5份有效来源。涉及3商家/多渠道地区，不能宣传为100份真实企业政策。

## 检索与回答发现

|配置|开发必要条款齐备|复核必要条款齐备|跨目标渠道片段数（开发/复核）|
|---|---:|---:|---:|
|全局向量|10/12|10/10|49 / 41|
|全局混合重排|10/12|10/10|36 / 21|
|明确绑定版本＋混合重排|12/12|10/10|0 / 0|

另有2道无订单事实题，不计入复核的10道政策证据题；三种配置均会给这2题返回相关政策。这不是事实证据。版本绑定来自场景元数据，不能算系统自动识别了商家、地区和渠道。

48次请求均结构有效，未超时、无重试；这不等于48题语义正确。按官方高峰未缓存输入2元/百万、输出8元/百万保守估算，本轮0.134900元，累计账本4.700946元，非供应商账单。实际响应只返回`deepseek-flash`别名，不能据官方价格页推断本批服务端固定权重。参数为思考关闭、温度0、最多512输出token；回答调用耗时P50约1.83秒、P95约2.43秒，不含前置检索。48次授权已用完。

复核覆盖48份回答的助手语义初审，作者确认3项关键发现，不能宣称全部由人逐条确认：

1. `real-001`：全局版主结论正确，却把零售店限制附加到线上订单；作者确认记录适用范围错误。限定版本避免了该例的跨渠道附加断言。
2. `real-008`：全局版把大陆发票流程与台湾排除条款合并；作者确认同时记录题面缺地区与回答混用。
3. `real-014`：摘要遗漏软件许可特例，限定版本仍将一般条件写得绝对；作者确认区分语料缺项与回答强化。范围过滤无法补齐被摘要删掉的例外。
4. `real-009`全局版有附加品类举例错误；`real-024`限定版建议提供会员卡号，但本系统无该查询能力，属于不必要信息请求风险。两项仍为助手初审发现，未追加人审确认。

9道题缺少明确商家或地区，原题、原始结果和原标签不改，后验质量复核单列，不用于“明确渠道下版本优劣”的15题对比。完整列表见复核JSON。没有据复核组修改检索器或再跑付费样本。不报告小样本线上准确率、统计显著性或全面净改善；当前证据只支持优先保留可信渠道绑定，同时补齐必要例外和信息不明确时的澄清。

## 一万片段容量结果

100篇确定性合成Markdown，每篇100个真实解析片段；不同商品、核验码和数量/期限条件，模板相似，专用于容量，不能算真实语义多样性。用全新实验MySQL库、独立账号绑定实验容器具体IP、独立组织和文件路径；只授权新库必要权限，不改变通用账号或演示库。

|档位|SQL / Chroma片段|本档新增上传入库发布前处理耗时合计*|暖查询P50 / P95|固定探针|
|---|---:|---:|---:|---:|
|50篇|5000 / 5000|292.84秒|3.18 / 3.59秒|9/9|
|100篇|10000 / 10000|新增5000片段264.98秒|3.04 / 3.21秒|9/9|

*计时包括上传与解析/向量入库检查，不包含紧随其后的发布调用。完整主体运行691.70秒，另记模型预热29.94秒；导入耗时未全部计入。每档只有3个目标×3轮，其中6个暖样本，P95按nearest-rank计算，不是压力或并发试验。原runner对小样本P95使用floor，原始值保留，派生报告更正，最终入口已修正。首查询含快照构建为4.19/4.81秒。峰值进程RSS约1.61GiB，包含模型与此前真实政策实验，非整套容器总内存。Chroma共享卷结束时约54.92MB，不能称单次实验增量磁盘。

两档均通过重复上传去重、实际文件哈希与SQL/向量数量核对。撤回指定版本、其他组织指定版本、未发布以及过期版本查询均为空；撤回的实验文档随后恢复发布。边界检查额外增加2个独立哨兵片段，因此最终实验库总SQL片段包含这些哨兵，不能把最终总数和容量档位混淆。原演示库仍是清理后保留的3条申请，质量更正订单申请数0。

## 复现与限制

```powershell
.venv/Scripts/python.exe scripts/evaluate_rag_real_scale.py validate --output work/rag-real-v1/validate
# 在已有本地模型与HTTP Chroma的实验容器中设置独立库的WORKBENCH_DATABASE_URL。
# 仅新建空库，名称必须匹配commercedesk_rag_accept_加10位十六进制；自行设置实验输出目录。
python scripts/evaluate_rag_real_scale.py run --database-name commercedesk_rag_accept_0123456789 --version both --output /app/work/rag-real-scale/new-run
.venv/Scripts/python.exe scripts/evaluate_rag_real_answers.py validate --traces docs/fullstack/rag-real-scale-evaluation/real-traces.jsonl --output work/rag-real-v1/model-preflight
# 不再执行run：本轮48次授权已经耗尽；需新的明确授权。
```

入口支持dataset/version（检索版本组合）/baseline/output/case-id，拒绝未知或重复编号、旧输出目录、非隔离库名和已存在业务表的实验库。生成入口是独立48次/1元授权批次，不提供无限重试。运行时网络失败或用量未知保留预留金额；没有发出额外修复模型请求。预算写入沿用原100元账本，未重置累计费用。

实际运行源码快照以`*.evaluated.txt`保留并与manifest哈希核对；最终入口随后只格式化、增加空库拒绝与修正P95统计，不伪称用最终字节重跑48次模型。4项评测边界单测通过，Ruff通过，MySQL实测空库拒绝通过。现有产品业务代码未改，没有为此再跑整套业务测试。

下一步按用户指定做源码走读与面试练习；业务改造候选是可信商家/地区/渠道绑定、不完整政策的必要例外清单，以及条件不明确时澄清。长上下文与部署缺项排在走读之后，不因本次容量通过便默认增加框架。
