当前功能：向量存储适配与隔离重建已接通，默认仍为原Chroma，操作见vector-storage-v1/README.md。后续进入本地交付收口；路线中的独立后端性能/质量对照须另行确认，不擅自执行Git操作。

当前开发顺序：政策提取与独立审核发布已接通（见policy-extraction-v1/README.md），接下来多Agent深化 → 知识更新联动 → 全链路追踪 → 向量存储适配。以功能交付为主，评测另行确认；以下为历史记录。

## 2026-11-02 最新：任务澄清、跨章节与有限回答协作阶段交付

代码仍在D:/VScodeProgects/commerce-desk-agent；main基于已合并66a0fad，本轮改动未commit/push。见docs/fullstack/return-inquiry-v2/report.md、answer-repair-v1/report.md、knowledge-collaboration-v1/report.md。

新增模糊任务choose_task/choose_fact、只读资格判断、咨询转新申请重核；双向明确跨章节关联及未读/未映射边界；最多一次修复再核验；取证/回答/复核LangGraph角色与独立私有检查点。共享2读4000字符1补检及调用/token额度，不扩大业务权限。较广107pass、最终协作14pass，两批重叠；三个专项浏览器各1pass（不同阶段快照），最终协作1pass。默认rules/excerpts、阅读关闭、single，新增修复/协作仍实验。

6个作者预备新家族的真实两版本比较：规则6/6各自走完，模糊任务基线6个均误入申请预览、候选5/6只读完成而1个被审查挡住；必要句12/12两者相同，不称RAG净改善。阶段4新3题三模式均3/3程序目标但无自然修复触发；阶段5新3题两模式各3/3程序目标，未证明协作收益。人工争议复核仍待回复，模型批准不是正确率，旧题转回归。未读封存24条最终测试题。

本次较上一停点新增70次真实请求、0.197486元；原128次/1元授权累计98次、0.273756元，100元总账5.030286元、未知0，剩30次/约0.726244元，不能重置或重放已付费题。仅新编演示资料与隔离字段。

日常四后端64源码/前端一致，34表/3申请保留，无迁移、无真实key。标准镜像构建遇Docker Hub超时，已用本地镜像离线增量构建发布；全新环境验收仍未完成。临时验收环境随后停止，不删业务库。总结27统一涵盖本次大阶段，下一篇28。源码走读仍暂停。

下一阶段6：带来源定位和未知/冲突标记的政策候选提取，经独立人工审核再联动发布/检索/FAQ；先冻结候选结构、正常/失败与维护成本标准，复用现有修订工作流，不让抽取值直接决定资格。阶段7候选向量后端隔离对照、阶段8并发/新环境与交付仍未完成，不提前勾选。保持业务Agent＋RAG重心。

以下为历史记录。

## 2026-11-01 最新：退货任务目的、有效政策复用与输出聚焦修复

见docs/fullstack/return-purpose-v1/report.md。明确规则咨询独立只读政策工具，无需订单/商品状态；事实字段直出，未知能力不查政策；更正后订单/质检重查，政策同绑定重新校验才复用，累计阅读预算不重置。按命中位置阅读、针对缺条款补检，无损合并重复元数据；两旧超限输入最终5754/5731，原上限6000。任务路由仍是明确措辞的保守识别，不声称通用语义理解。

较广96pass；最终专项61pass、MCP33pass（重叠不累加）。真实工具链旧16题均到目标状态/0多余暂停，原评分器15/16；011已复用校验但旧计分只认当轮新搜索/阅读，原分数不覆盖。4开发题真实8请求，1条多余核实条件已用户确认，语义审查漏检；补程序与生成Schema硬约束并验证公开隔离，约束后自然模型未重测。没有独立净改善结论，不扩大默认启用。

本次0.017968元；该授权累计28/128、0.076270/1元；总账4.832800，未知0。四后端63源码一致、本地API/web可用，3申请/34表保留；评测容器已停，无commit/push。总结26完成，下次大跨越27。后续重点：模糊目的澄清、跨章节必要例外、规则咨询到明确办理及独立新家族对照；保持Agent＋RAG业务重心，源码走读暂停。以下记录为历史。

## 2026-11-01 最新：新退货问题整链路诊断完成，未修改生产代码

见docs/fullstack/return-journey-v1/report.md。16条预冻结新题/8家族，32次真实流程；补列4条后验阅读探索/8流程，严格分开。主集两路径程序目标均10/16、当前轮RAG必要句均14/16、各3题多余补问；001误入办理、006附加非所问字段已由用户确认。探索5次原文阅读中1次当前问题无必要、1次同版本同窗口重读。发现数量更正清掉仍有效证据且预算耗尽，必要句已取得却因生成输入超限无法回答。无净改善结论，阅读仍默认关闭。

真实模型20请求（主15+探索5），保守0.058302元，累计4.814832元，未知用量0；本轮授权上限128次/1元，未用满。9个评测器测试通过。原始summary和当前轮覆盖重评分v2分开，生产62模块、主题集和协议哈希均未变；日常3申请保留，评测库0退货申请，自建评测容器已停。无commit/push，源码走读暂停。总结25统一涵盖本次诊断，下一次大跨越26。

下一阶段先冻结未用于修复的新题，再集中修复任务目的识别、问题级证据收敛、同源政策更正后重新验证复用和输出聚焦。现16+4题转回归；不要只给个别句子加关键词或临时扩大预算后称独立提升。继续按完整阶段统一总结，测试临时目录留D盘。

## 2026-11-01 最新：知识修订—审核—检索/FAQ换版闭环交付

见docs/fullstack/knowledge-revision-v1/report.md。管理员按段修订、保留原件和差异；独立approver审核后管理员发布，重建父子块/索引。FAQ依赖持久失效，人工新版本复核后恢复，回退不自动复活；覆盖索引中途来源变更和无效回退。较广79pass、最终相关36pass（重叠不相加，含15修订专项），最终候选真实工具链浏览器1pass。四后端62源码/web一致，34表、原3申请保留，无付费调用、迁移或commit/push。默认rules/excerpts，阅读实验仍关闭。新版本保存解析快照，旧无快照版本只能按当前解析器重解析。总结24统一涵盖整阶段，下次大跨越25；不要为测试/界面修补/文档收尾新增总结。后续独立退货整链路对照，源码走读暂停。后续测试显式使用D盘work下临时目录。以下为历史记录。

## 2026-11-01 最新：退货Agent按需原文阅读工程交付

见docs/fullstack/source-agent-v1/report.md。新增有界read_source动作：当前命中/明确关联/下一页目标，最多2读4000字符、1次补检，额度持久化；更正清证据不清额度；真实字段缺口仍补问或交还业务核实。95项较广回归通过，最后专项18通过（重叠不相加），真实工具链浏览器1通过，规则模式0付费调用。四后端61源码一致，默认阅读关闭/rules/excerpts，无真实key，34表与原3申请保留，无commit/push。完成的是退货垂直阅读工程能力，尚无新真实模型净改善结论。总结23完成，下一次大升级24；源码走读暂停。后续围绕来源修订与独立决策评测，以下为历史记录。

## 2026-11-01 最新：阶段2回答核验与发布隔离完成

详见docs/fullstack/answer-gate-v1/stage2-report.md。最终相关55pass（与此前79pass有重叠）、浏览器1pass；本地四服务60源码一致、34表。32次真实开发调用已耗尽，保守0.055584元、累计4.756530元。8自然回答获审查通过、8注入错误被拦截；用户确认2项自然回答存在多余补问/不确定提示或非所问字段，均为审查漏检。不默认启用：日常保持原文展示/rules，无真实模型密钥安装。无commit/push。下一步阶段3：有界搜索与原文阅读决策；源码走读暂停。总结22完成，下次较大升级23。以下记录为历史。

## 2026-11-01 当前：阶段1本地工程交付

以docs/fullstack/rag-upgrade-v1/stage1-report.md和stage1-evidence为准。父子分块、明确条文关联、表格上下文、原文阅读、显式范围和FAQ来源失效已贯通；最终相关67pass与真实浏览器1pass。独立MySQL/真实BGE/Chroma/重排/HTTP MCP通过；4份商家有限摘录不等于完整政策。当前四服务58源码及web匹配，0009/34表，原3申请保留，默认rules/neighbors，无付费调用或commit/push。独立语义净改善与完整商家覆盖未完成。

下一步阶段2：证据需求、结论契约、语义检查及发布隔离；先复用候选，未获新调用授权不发真实模型。Agent自主阅读在阶段3。源码走读暂停。总结21已保存，下次大升级从22编号，写前核对。

## 2026-10-30 总结写作要求更新

用户要求每项独立任务完成后写通俗总结；同一任务小修补充原篇。1—16篇原位改写，导读与目录更新，17记录此次文档整理，下一篇18。未修改业务代码或运行配置。当前产品仍待并发与追踪验收，源码走读暂停。细则见task-summary-convention.md。

## 当前优先级（2026-10-30）

用户暂停源码走读，长上下文第一版已完成，见long-context-report.md。后续原路线缺项是统一运行追踪、10并发会话/2文档任务验收及部署收口；没有Linux服务器。不要把本地顺序一万片段测试称并发生产容量，也不要继续无限加可靠性组件。原业务Agent＋RAG重心保留。下一次大总结17。

真实模型48次授权已耗尽，本轮新增调用0，累计账本4.700946元。源码走读需后续用户安排。新代码和上一轮RAG结果仍未commit/push。以下旧计划为历史记录。

## 当前优先级（2026-10-29用户指定）

真实政策与10000片段本地验证完成，见rag-real-scale-report.md；不能宣称完整真实政策或独立人审。接下来做源码走读/面试练习，沿退货状态、更正失效、工具约束、证据组装走完整链路，再处理长上下文/部署缺项。5份摘要缺完整软件例外；9道缺商家地区的题单列。新模型48次授权已耗尽，禁止追加调用。新实验本地未commit/push。

# Latest checkpoint - 2026-10-29

后续授权更新：用户已明确要求审阅PR并在检查通过后合并主分支，使默认页面、代码和演示入口一致。下方“不合并主分支”为旧轮次当时的边界，已被这次授权更新。另已授权亲自演示、源码走读、真实多样政策及一万片段RAG验证；新增付费调用仍需按原费用与外发范围规则另行确认。

版本保存说明：用户已授权将已完成的阶段整理成本地提交并推送到现有`feat/fullstack-foundation`分支，不合并主分支。下方“未提交/未推送”是各次实验当时的记录；实际版本以Git提交为准。候选哈希清单是提交前快照，不是提交ID。版本保存不新增产品精进编号，下一篇大阶段仍从15开始。

## Newest completed checkpoint: stage14 / local portfolio handoff complete

Authoritative D:/VScodeProgects/commerce-desk-agent. Original four-track LOCAL portfolio scope now closed: business return loop, bounded Graph, finite RAG experiments, isolated recovery plus demonstration/docs. Entry docs/portfolio/README.md. Three existing browser stories replayed on fresh labelled demo data3/3pass0retry46.87s; unknown-facts framing followup1pass11.6s overlaps. Screenshots reviewed by assistant, no independent human review. Runtime workbench54modules/migration0009/34tables/default rules+neighbors/no model key/webbuild matched. See delivery-checks.json and delivery-manifest.json for final inventory.

Main private replay work/portfolio/20261029T105441Z_7c0da5c6; logs work/portfolio-stage. run_portfolio_demo.py --prepare-only for fresh manual data or no flag for3browser stories. Leaves fresh fixture pointer in work/returns-v1/demo.json; previous pointer retained. Playwright optional WORKBENCH_PORTFOLIO_SCREENSHOTS=1 enables endings, otherwise failure-only. Trace/video off. Unsupported CLI flags first failed before cases; wrong cwd credential path caused framing attempt to fail before scenario; corrected, final pass, failures retained. No domain/runtime/schema changes, paid calls, commit/push.

Root README now current; old architecture/demo pages and install checkpoint explicitly historical. Major summary14 saved after checks, next15 only for another substantial authorized advance (verify actual directory). Do NOT automatically add new business/framework/reliability stages now. Next: user practice and feedback using demos/interview docs, then choose a new goal. Production/server/platform work not authorized by completion claim. Known limitations remain: model strategy unproven, short-query rerank miss, finite authored corpus, MCP audit run correlation absent, dropdown refresh lag, no actual refund/production readiness. Never read sealed holdout, auto-run paid tests or publish credentials.

## Newest completed checkpoint: stage13 / isolated application and RAG recovery

Authoritative D:/VScodeProgects/commerce-desk-agent. Newest full-recovery-report.md/recovery-checks.json take precedence. Original step4 recovery acceptance COMPLETED:34tables,62original files,30active published versions/30SQLchunks, fresh separate Chroma, independent DB account denied SELECT on source. Same run/pause restored, condition supply/preview/independent approval/simulated receipt completed. Replayed resume keys leave1application/1receipt event. Restarted recovery services preserve receipt. Recovered withdrawal excludes sources, original full34table snapshot unchanged and original run still awaiting_input.

Private drill work/recovery/20261029T104123Z_be491a4b; backup path in its state/backup.log. Private SQL/accounts/checkpoints stay ignored. Four recovery services stopped; DB/files/index/backup retained. Source acceptance stack unchanged. No paid calls, migration, commit or push. Public authored trace/results/executed sources/candidate hashes recovery-evaluation/. Completed resume now no-op; invalid directory rejected before mkdir. Full invocation executed before these2entry guards; exact snapshot retained and follow-up tests separately recorded.

Next only remaining original step4 portfolio handoff: three truthful demonstrations (normal return, unsupported facts/evidence, branch/correction constraints), current architecture, local startup/troubleshooting, experiment limitations, interview story. No new business/frameworks/reliability expansion or paid calls. Known limitations: same host/shared MySQL/same version/paused workflow only; no production RTO/RPO/live refund. Current authored run events trace MCP steps, but wb_tool_invocations.run_id query returns0; do not claim complete cross-table correlation. Major summary13 saved after checks, next14 verify directory.

## Newest completed checkpoint: stage12 / purpose-aware evidence composition

Authoritative project D:/VScodeProgects/commerce-desk-agent. Stage12 report rag-purpose-report.md and checks rag-purpose-checks.json take precedence. No new paid calls, commit or push; sealed holdout untouched.40 authored tasks (32QA+8application variants),6fixed parent contexts,3strategies=720rows. Question minimum evidence4/5 raw,3/5 forced projection,4/5 purpose-routed;8application wrong-branch contexts4/8->0/8 at tier180 references. Bundle0/8 unchanged: no recall or generated-answer-quality gain. Explicit existing read-only route and knowledge entry bind purpose; no new general intent classifier. Source validation and deterministic eligibility/approval/write guards retained. Short photo browser query reranked to no evidence; original failure retained, richer final demonstration does NOT fix it.

SQLite270pass6skip, MySQL purpose5pass; see checks for final container/browser counts and initial failures. Runtime rules/no model key/neighbors, migration0009,34tables. Public frozen replay and source proof rag-purpose-evaluation/. Major summary12 saved after final checks, next13 (verify directory).

Next original step4: freeze a local delivery candidate without automatic commit, reuse existing backup machinery to restore into NEW isolated DB/storage/index only, rebuild vectors from SQL source and test pending flow continuation/approval/simulated result. Do not overwrite active DB, remove volumes, add more paid calls, invent production readiness or add unrelated business/frameworks. Preserve limitations and prepare3demonstrations plus interview material.

## Newest completed checkpoint: stage11 / long-document and bounded-scale RAG

Takes precedence over historical stages below. User explicitly redirected to original step3 long documents/scaling, not new business/reliability. Authoritative D:/VScodeProgects/commerce-desk-agent. No new paid calls/commit/push; exhausted stage7 grant not renewed. Never read sealed holdout. Major summary11 saved after checks; next12, verify directory.

- Frozen protocol rag-long-scale-protocol.md. Initial fixture rag_long_scale_v1 exceeded8k-15k character range (~19k), excluded BEFORE retrieval and retained with generator snapshot; v2 uses3 instead of4 operating paragraphs/chapter,6longdocs14648-14652chars.32 authored dev questions,180template docs, tiers12/60/180; no independent blind set.
- New reference_context.py: actual retrieved canonical anchors only, same org/version within caller's permission-checked snapshot, explicit Arabic article refs,2hops/8chunks/character budget. Missing/limits in trace; complete means parsed references only, not all policy conditions. Settings adds experimental references; KnowledgeRetriever integrates and still revalidates active publication after inference. Compose passes mode, defaultneighbors unchanged. No new service/migration.
- Full local BGE/BM25/RRF/CrossEncoder comparison:8arms*32questions*3tiers=768/768,2180.62s. Snapshots/hash/model revisions/versions/traces persisted docs/fullstack/rag-long-scale-evaluation; model generation/provider calls0. Tier180 has134active merchant docs,139206chars total, structural600468chunks (230active),structural300612, fixed600336; not enterprise/real-corpus scale. Peak memory not measured; indexes13360308bytes.
- At180: globalstruct600 process bundle0/24,mean23.1%,wrong-document fraction50.8%; scoped0/24,46.0%,wrongdoc0; scopedreferences9/24,58.7%,wrongdoc0. Same full counts across tiers. References has4limited cases/tier, wrong-branch quote cases7->8 vs scoped, so NOT auto-enable. Candidate pool24 adds no full/mean gain at180 butp50~4.77s vs2.63; top5 modestmean26.2%,still0full. Timings confounded by cache/concurrent checks; scoped BM25 prepared perquery vs production snapshot cache. No generated-answer accuracy claim.
- User explicitly confirmed author-participant review: photo question long-dev-032 is policy-rule-answerable, not unsupported; scope-only questions should separate minimum-question evidence from full process bundle. Original labels/scores unchanged. rubric data/evaluation/rag_long_scale_review_v1/rubric.json; rescore script verifies frozen cases/hash; review-full.json only5case criteria, corrected negatives7. scoped &refs both minimum evidence4/5 vsbaseline2/5.7anonymous actual evidence pairs prepared but NOT individually human-scored. No independent blind review.
- Long source picker previously truncated tofirst20. return_api now limit<=20+offset+has_more with existing admin/org/version/publication checks; React loads additional pages. Real long MD upload/worker/publish/source pagination/browser selected article20 original exclusion text, did NOT auto-register all complex policy semantics. Role/invalidation regression tested. Unmodeled exclusions still need human judgment.
- SQLite full258pass6skip693.34s before final pagination; focused refs/review/CLI17pass6.10s; final pagination+existing returns+refs/review23pass1skip43.04s (overlap). Actual MCP+MySQL1pass8.69s; container regression2pass42.50s; long browser1pass22.8s. Ruff/TS/Vite/diff pass. Current api/mcp/run-worker54src files +0009migration match host, web index matches, rules/keyabsent/mode neighbors. No new schema;34tables.
- Guide rag-long-scale.md, report rag-long-scale-report.md, checks rag-long-scale-checks.json, full tables/anonymized evidence rag-long-scale-evaluation/report.md. Private logs/index work/rag-long-scale-v1, existing backups retained. Current-stage disposable test temps cleaned only after processes ended. No customer records/keys published.
- Next prioritize remaining actual-evidence semantic review and a controlled composition check (references then existing branch projection), verifying policy Q&A does not suffer false refusal. Then original step4 full isolated restore/app continuation/vector rebuild and portfolio handoff. Do not claim step3 generalized/enterprise-proven or add unrelated frameworks/new business. No extra model calls without renewed explicit request-count/data authorization.

## Newest completed checkpoint: stage10 / explicit defect-return branch and clause projection

Takes precedence over historical stage9/8/7. Code remains D:/VScodeProgects/commerce-desk-agent, uncommitted/unpushed. No new paid calls, original24-request grant exhausted. Never read sealed holdout. Latest major summary10, next11 (verify directory).

- Explicit return_basis no_reason/quality_issue persisted on ReturnRequest, propagated through RunInput/FlowResume/graph/MCP/UI; policy mismatch blocked. Quality-issue rules require unused=false, explicit quality clause, required quality fact, requirement defect_confirmed. Demo branch30days vs original7days; no automatic legal-policy inference. Corrections re-gather latest facts and clear old policy context.
- QualityInspection finding independent of verdict: defect_confirmed/no_defect/unclear. New explicit finding must match failed/passed/inconclusive respectively; old finding remains null, old failed never becomes confirmed defect. Missing defect finding pauses guided graph quality_wait; negative finding blocks selected quality branch. Approval and simulated receipt still recheck.
- Additive migration0009_quality_return_basis adds2columns,34tables unchanged; startup and CLI version updated. Default no_reason serializer strips new key to preserve old default application hashes; RunInput default also excluded from hash. Existing quality-required previews may require rechecking after new finding field. Pre-upgrade backup work/backups/20261029T082155Z_e9a83e4c stable, no restore performed. No destructive rollback.
- New return_context.project_policy_context derives quotes ONLY from actually retrieved validated chunks + operator-bound policy refs, with source hash/offsets; cannot add missing sources or fall back to full context when empty. Experimental profile clause_bound = version restriction + quote projection. Decisions use projected context, public full citations retained separately and revalidated. Default experiment not auto-published; runtime remains rules/no key.
- Frozen24 authored dev questions/3docs; primary real-local embedding/BM25/fusion/rerank48traces: necessary full coverage9/20 both; predefined wrong-branch quote cases17/20->0/20, context226.21->89.88chars. No recall gain and no generated answer accuracy claim.
- After primary results wrote separate supplement protocol, same-dev exploratory48traces with at most one business-ref query (not scoring gold). Full coverage20/20 both; wrong quote cases20/20->0/20.15/24 cases require extra retrieval. Unknown-fact policy returns1/4 primary,4/4 supplemental: policy still not actual facts. Timings confounded by concurrent work/cache. Raw/scores/manifests/executed source snapshots retained docs/fullstack/quality-return-evaluation, snapshots .py.txt; do not overwrite primary or pretend independent test.
- Final SQLite244pass6skip643.31s; affected MySQL48pass426.42s; container API10pass115.90s/browser20pass2.2m. After final neutral window wording focused11pass28.23s; after configuration mode label browser2pass31.1s (overlap). Ruff/TS/Vite pass. api/mcp/run-worker53 source files +0009 migration match current host, web index match; final proof saved.
- Guide quality-return.md; report quality-return-report.md; checks quality-return-checks.json. Logs work/quality-return-v1. Current stage disposable temps cleaned only after all tests; local index, cache, backup, DB and evidence retained. Summary10 saved to Obsidian major-summary directory; no commit/push.
- Next prioritize author-assisted key semantic case review, then choose one concrete quality-reason/processing/responsibility/shipping-fee branch. Not arbitrary natural-language policy understanding, real inspection, image defect detection, refund settlement or physical-unit traceability. New real provider requests need renewed explicit request-count/data authorization. Avoid unrelated reliability/deployment frameworks.

## Newest completed checkpoint: stage9 / inspection work orders

This takes precedence over stage8/7 below. Authoritative project D:/VScodeProgects/commerce-desk-agent; no new commit or push. No paid calls authorized beyond exhausted stage7 request grant. Never read sealed holdout.

- New quality.py/quality_api.py/quality_schemas.py and two tables in return_models.py: operator-recorded DEMO inspections, not external physical inspections. requested->in_progress->completed; admin void. Support creates with idempotency; approver handles, creator cannot handle own record, assigned operator completes. Scoped by org/order/item/exact quantity; latest pending/voided/inconclusive never falls back to old pass. Revision CAS + parent/item locks + events/audit.
- Alembic0008_quality_inspections,34tables incl Alembic; additive only. Updated startup and CLI version. Empty tables may downgrade for isolated tests; populated tables block rollback. Pre-upgrade SQL/document backup work/backups/20261029T060514Z_0c63411e, stable, no restore performed this stage. Do not delete backup or business data.
- Return rules required quality supports recorded vs passed; optional quality_clause validated alongside existing source refs; new admin UI requires selecting source for quality mapping, legacy rules retained. Semantics manually mapped, not inferred. Quality record/revision included in preview hash; confirm/approve/submit re-evaluate. No changes to real refund capabilities.
- MCP get_quality_inspection READ ONLY; fixed order/item/quantity args, gateway validates provenance/binding/status/verdict/revision. Guided_v3 queries only when policy requires or user asks quality. quality_wait interrupt only refresh; re-gathers latest facts and policy. Read-only queries stop, never application confirmation. Normal return does not add quality tool. Model cannot record result or approve.
- React QualityInspections component in Returns: support creates, approver starts/completes, admin voids, event history; pending Agent refresh. Disable inputs during async save/refresh to avoid cleared notes. Seed demo now publishes normal and quality-pass branches with actual source clause; original orders retained.
- Final SQLite233pass6skip481.84s; MySQL affected37pass298.39s +3 added cases22.32s. Final container API10pass74.99s and browser19pass1.2m, new quality case8.2s. Ruff/TS/Vite/diff checks pass. api/mcp/run-worker52src files +0008migration match host, web index match; rules/no model key.
- Initial startup schema, migration-empty rollback and browser async failures retained; fixed and final whole suite rerun. Logs work/quality-v1; proof docs/fullstack/quality-evaluation/image-proof.json. Guide quality-inspections.md, report quality-implementation-report.md, checks quality-checks.json, preimplementation criteria quality-stage-protocol.md.
- Summary9 saved after completion; next major summary10 (verify directory). Disposable current-stage test temps cleaned; evidence/backup retained. No new paid calls or sealed tests, no online accuracy claim.
- Next prioritize quality-problem return branch and same-document exceptions/conditions/corrections RAG comparisons, domain requirements first. Physical unit allocation, real providers, auto notification/SLA and separate quality role are NOT implemented. Do not expand reliability/deployment framework without business need.

## Newer completed checkpoint: stage8 / guided_v3

Stage8 complete, still uncommitted/unpushed in D:/VScodeProgects/commerce-desk-agent. Latest guide/report/checks: return-intake.md, return-intake-report.md, return-intake-checks.json. Historical stage7 notes below remain for provenance; this section takes precedence.

- Guided v3 adds source-grounded literal condition candidates, explicit quote confirmation, stale-proposal guards, unknown/conflict handling and explicit clarification/correction provenance. Not general NLU. React exposes description/query/confirmation/fact cards and admin required-fact mapping.
- Explicit read-only requests stop before application; mixed application + unavailable bank fact can still preview valid application. Related policy snippets do not establish actual fields. Only signed_at currently has a trusted field; activation/member/quality requirements block confirmation in business API as well as Agent.
- Model runs only when multiple actions legal. v2 preserved; missing raw marker remains legacy. New profile schema defaults guided_v3, persisted profiles retain snapshots. Runtime remains rules, key absent. Admin can publish guided_v3 from configuration page; existing profiles not silently upgraded.
- Frozen authored development24 conditions +12 fact questions, all36pass. No holdout. Original24 provider trace replay11 skippable; historical0.009396CNY is counterfactual, no actual savings/refund. New paid calls0. Previous24 request grant exhausted; no additional model requests authorized.
- Full SQLite212pass6skip; final focused10pass overlaps full and adds3 direct API guards. MySQL affected41pass plus3 new direct API guards. Final container API10pass87.82s, browser18pass1.2m. Ruff and host TypeScript/Vite pass. api/mcp/run-worker49 source files each match host, web index matches;0007/32tables unchanged.
- First clarification and browser failures fixed and logs retained. No final human semantic quality review or online accuracy claim. Exact proof copied to return-intake-evaluation/image-proof.json. Private logs/DB/keys remain work; only authored/synthetic evidence public.
- Obsidian summary8 saved after checks; next major summary9 (verify actual directory before writing).
- Next business increment: trusted quality-inspection work-order fact lifecycle, domain API then MCP read adapter, then Agent resume from actual result; extend same-document conditions/exceptions/corrections RAG controls. Do not resume generic reliability/deployment expansion or add paid calls.

## Historical stage7 checkpoint

Authoritative project: D:/VScodeProgects/commerce-desk-agent. Base local commit1e8f369 on feat/fullstack-foundation; current stage7 changes are NOT committed/pushed. User prioritizes business-depth Agent + RAG. Ordinary continuation authorized; dangerous/destructive commands require decision. No Linux server/purchases. Prefer D caches/temp. Never read sealed holdout or expose credentials. Only major advances get Obsidian summaries; newest7, next8 (check directory first).

Newest completed: bounded return observation/decision/tool loop + policy-scoped RAG experiment.
- return_decisions.py strict discrete actions; model never owns args, eligibility, money, approval or writes. Invalid/timeouts/quota fall back to legal rules, never publish model draft.
- return_observation_graph.py new observed_v2. Read order/search/check/supplement/ask_condition/review/stop; existing confirmation/approval/write nodes reused. max one supplemental search.
- New ProfileOptions return_flow_version observed_v2, return_decisions rules/model, return_retrieval_mode baseline/policy_scoped. Separate return_prompt_sha256; snapshot/template/model binding protection. Old values lacking flow marker retain old graph. Published defaults still rules/baseline.
- Per-run model attempts max6 only for explicitly enabled return profiles; snapshot created before usage row. 16384 token limit and8192 reservation retained. Unknown stays reserved, preflight releases tokens but not attempts. FlowAction observation+prompt cache prevents unknown replay. Guard authorization/lease before and after calls.
- SearchInput.version_id narrows active organization/published/effective sources only; production scoped mode derives version from validated business policy, not model or evaluation gold.
- FlowResume can correct policy binding; observations reset and current policy fingerprint changes. Frontend controls/decision trace/stop-follow/current corrected record restoration.
- No schema migration;0007,32 total tables including alembic. API/MCP/run-worker47 source files each match final host; web index matches. Runtime flags bothrules, no configured model key. Seven local services API18082/Web15173 remain, portable MySQL33316 retained.

Verification:
- Full SQLite166pass6skip463.34s. MySQL affected61pass420.65s. Last HTTP response cap/deadline2pass0.12s (one overlapping/new; don't sum). Not a final full MySQL suite claim.
- Final container API10pass73.49s; browser17pass(1.0m). work/return-agent-v1/container-test-final.log. Real scoped-policy observation loop, server model cannot be enabled from frontend, original upload/approval/SSE/history/restoration covered.
- First new scope test failed because its query wasn't in unpopulated dense fixture. Corrected to actual clause query, kept org/withdrawal/expiry assertions. Initial failing logs retained, no weakened checks.
- Ruff/TypeScript/Vite passed; final image-proof.json. HTTP read deadline finished after paid batch and tested with substitutes, no more paid requests.

Frozen experiments:
- docs/fullstack/return-agent-stage-protocol.md and data/evaluation/return_agent_v1/ hashes frozen before candidate.24 dev questions/6 families/6 demo docs/36 structural chunks,8 observation-contract cases. Never sealed holdout.
- Real local vector/BM25/fusion/rerank,48 results. baseline necessary full clauses16/18 -> scoped18/18; different-policy document fraction27.36%->0%; mean context371.21->280.79chars(-24.36%). Both unsupported questions6/6 still return snippets. Same-document applicability and answer sufficiency unresolved; no generated answer accuracy claim. Scope binding manually known, not automatic inference.
- User explicitly approved DeepSeek official endpoint and only artificial isolated fields after auto-review initially blocked. Scope recorded in docs/fullstack/return-model-data-authorization.md.
- Real provider batch24/24 requests complete, legal24, errors0, unknown0.8 standalone contract observations +3 model flows4/6/6 requests. Rules/model same pre-write outcomes in3 development cases. Two model flows accepted_simulated; used-condition correction stopped without request. API TestClient, actual HTTP MCP, actual local persistent Chroma/BGE/rerank, isolated SQLite. Not every service independently deployed.
- Model response aliasdeepseek-flash, not immutable weights. thinkingoff/temp0/max256/no retry. Official pricing checked2026-10-29: peakinput2/output8CNY perM. Batch0.019846CNY estimate, originalledger total4.566046. Shared5CNY pool includes old0.026836; no reset.
- IMPORTANT: current stage24-request authorization fully consumed. evaluate_return_model.py refuses further paid requests. NO additional real calls without a new explicit request-count authorization, even though original100/5CNY monetary balances remain. No daily runtime paid mode enabled.
- Raw synthetic evaluation artifacts copied to docs/fullstack/return-agent-evaluation (no DB/key/token/private env). Private original logs/isolated DB/index/budget remain work/return-agent-v1 and work/answer-trust.
- Cold rule normal baseline12s vs warmed model8s isn't a speedup claim. Other same pre-write phases rule3.86/4.16s vsmodel10.91/11.75s. Many model states have only one legal action, redundant calls identified; no model task advantage shown, defaults off.
- Obsidian7_从固定退货流程到受限模型决策与政策范围检索.md. Main report return-agent-implementation-report.md, guide return-agent.md, checks return-agent-checks.json.

Next: keep business Agent + RAG focus. First reduce redundant model calls when only one action is legal (offline replay/contract checks possible without new paid calls), then target ambiguous free language/conditions/multi-intent where model may actually help. Distinguish related sources from evidence for the requested fact, retain rule baseline/normal controls, expand same-document conflicts/exceptions/corrections and corpus scale. Human final semantic review remains pending; don't claim blind evaluation or online accuracy. Do not prioritize new standalone reliability frameworks or deployment components. Full isolated restore remains deferred, not next priority.
