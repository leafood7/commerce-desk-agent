# 阶段1：可读原文、范围澄清与FAQ来源联动

日期：2026-11-01。状态：本地工程闭环已交付；独立语义质量与默认启用验收未完成。当前工作台http://127.0.0.1:15173。未commit/push。

## 新增能力

1. 管理员可选择父子分块，保存原文父章节与检索子片段关系。Markdown标题和纯文本法规条文支持章节边界；旧文档不自动重写，需新增版本入库。
2. 检索可按商家、地区、渠道和商品范围筛选。缺少已标注的必要范围时澄清；更正字段清空旧结果，再检索。范围来自显式输入，不声称任意自然语言或订单自动识别。
3. 搜索子片段可关联父章节和同版本原文明示引用条款，中文/阿拉伯条文编号一跳、有界扩展。去重、字符预算、20片段上限，无法定位和预算不足显式记录。没有明示关联的隐含例外仍可能漏检。
4. Markdown表格子片段重复原始表头；DOCX重复原始首行作为上下文。父章节保留解析原文。DOCX首行不保证是语义表头，复杂合并表格、OCR与PDF坐标未完成。
5. 新增认证阅读API与MCP read_policy_source，校验组织、当前发布版本、有效期和提交的范围；返回章节、原文哈希、解析块位置、分页与部分读取标记。原文窗口可以在句中结束，但必须显示尚未读全。章节读完也不代表全部政策例外齐全。
6. 区分政策文档、产品资料与审核FAQ。FAQ绑定同组织、有效且范围匹配的非FAQ政策版本。来源更新、撤回或过期后，旧FAQ暂停检索和阅读；须审核新来源并发布新FAQ版本。未实现FAQ专用优先路由或通用语义编辑器。
7. 工作台提供上传范围回填、原文阅读、继续阅读、关联条款跳转和FAQ待复核提示。当前由客服点击阅读，Agent自主补读在阶段3。

现有版本表parse_metadata保存结构，无新增表或迁移。默认仍rules/neighbors，父子上下文模式通过服务端配置进行对照；不因工程通过而自动全局启用。

## 来源和实测

一份市场监管总局官方法规HTML原文，经确定性抽取38条条文。仅用于原文解析，不自动映射商家资格。原PDF端点403，改用官方HTML；初始误估37条，核对后改为38并去除页脚，修正记录保留。

四份商家官网有限原文摘录：Apple大陆官网、Apple大陆零售、宜家大陆、Decathlon美国，共3商家，约280字符/份。不是完整政策或正确答案集。公开来源、有限文本、范围、抽取窗口与哈希，完整网页只留私有work。Apple官网首次gzip解码失败，修正后取得原文，未用摘要补造。

独立MySQL空库与原演示库隔离，真实BGE、HTTP Chroma、BM25融合、本地重排和HTTP MCP完成上传、解析、索引、发布、范围检索及阅读。Apple缺渠道时澄清而不混合线上/门店。问题为面向摘录的开发探针，不是独立最终测试。

真实浏览器完成：上传发布 → 缺范围澄清 → 补条件检索 → 阅读原文 → 更正清空旧结果 → FAQ关联 → 源政策更新 → FAQ待复核与读取拦截。已检查实际页面截图。

## 最终验收与限制

- 受影响回归67通过，152.48秒。最终浏览器1通过，8.60秒。前期21/24/62等批次重叠，不累加。
- Ruff、TypeScript/Vite与差异空白检查通过。既有TestClient弃用警告保留。
- 5种合成布局×3模式共15条固定锚点回放，例外出现neighbors1/5、section1/5、parent_child3/5。未关联跨章节和紧预算仍失败。这是实现后组装诊断，不是召回率、回答准确率或独立收益验证。
- 当前api/mcp/worker/run-worker各58源码与宿主一致，web与dist一致。schema0009、34表不变；原3条退货申请保留，隔离验收0退货申请；当前只读查单冒烟通过。
- 无新增付费模型调用，不读取旧封存题，无真实退款、业务数据删除或生产迁移。
- 未证明普通问题不被额外澄清、任务完成度净改善或线上准确率提升；完整真实商家政策覆盖与独立最终语义测试仍待补。工程闭环不等于这些目标已完成。

## 失败与修复

新增默认资料类型使续传配置哈希变化，原去重测试失败；规范化空值和默认值后，重复上传恢复复用原版本。第二次浏览器验收因相同文件被正确去重而复用旧标题，夹具误以为应有新标题；最终为每次运行加入独立内容，不删记录制造通过。添加已有文档版本时范围未自动回填，已修正并重建前端。

## 证据与复现

公开脱敏证据在stage1-evidence，冻结基线在protocol.md/baseline.json，组装对照在stage1-assembly-final。输入在data/evaluation/rag_upgrade_sources_v1和rag_upgrade_merchant_v1。私有环境、原始网页、完整日志在work/rag-upgrade-v1，不应整体公开。

```powershell
.venv/Scripts/python.exe -m pytest tests/workbench/test_parent_policy.py tests/workbench/test_knowledge.py tests/workbench/test_scoped_mcp.py tests/workbench/test_profiles_conversations.py tests/workbench/test_return_rag_scope.py tests/workbench/test_return_decisions.py -q
.venv/Scripts/python.exe scripts/evaluate_parent_assembly.py --output work/rag-upgrade-v1/new-assembly-run
npm --prefix frontend run build
# 设置隔离API、工作台地址和私有测试密码后，从frontend目录运行：
npx playwright test rag-stage1.spec.ts
```

## 下一阶段

进入阶段2：按当前问题判断证据需求、结论与证据约束、单次语义检查和检查后发布。先审阅已有候选，复用可验证部分。真实模型生成需新的次数、费用和外发范围授权；离线结构与失败测试可以先做。并发与追踪按路线穿插，Linux及客服试用按条件交付，源码走读继续暂停。
