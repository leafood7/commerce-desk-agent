"""Post-run AI-assisted case review, accounting, and report. No network calls."""
import json
import re
import statistics
from pathlib import Path
import hashlib

P=Path(__file__).resolve().parent
def read(name): return json.loads((P/name).read_text(encoding='utf-8'))
def sha(name): return hashlib.sha256((P/name).read_bytes()).hexdigest()
def save(name,obj): (P/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')

# Judgments below are recorded only after every response has been collected.
# pass = satisfies prespecified required conclusions without material semantic overreach.
NOTES={
1:('两组均先核实开具、邮箱与拦截，不重复申请；均额外解释2工作日目标，相关但非必需。',True,True,True,True),
2:('两组均不迁移其他平台PLUS权益与费用，也未编造本商城年费。',True,True,True,True),
3:('两组均否定个人转企业无条件更改；开启组额外说明已开票与商家开票边界，证据支持。',True,True,True,True),
4:('两组均不将购物篮总额当券面指定商品金额；开启组对结算算法的描述偏具体，但问题已明确指定商品满200，按题设解释，未给独立系统保证。',True,True,True,True),
5:('两组完全同答，不保证运费统一列同票；开启并未增加片段（该文件三条已全在锚点）。',True,True,True,True),
6:('两组均保留活动特别条件。对普通规则仅适用于普通券的措辞偏宽，本题已明确另有活动条件，未改变结论。',True,True,True,True),
7:('均否定逐段累加，区分7日与工作日。关闭组末句核查7日内发起弱于政策要求的返还；记措辞风险，不据此否定其已明确的主结论。原关闭组首次调用断连，补测结果另存。',True,True,True,True),
8:('两组均区分自助窗口与永久资格，并将2工作日表述为有条件服务目标。',True,True,True,True),
9:('两组均纠正地区与运费券返还。均额外引用普通商品券部分退货规则说明运费券，有跨券种套用风险；由于没有作额外肯定返还承诺，作为质量告警保留。',True,True,True,True),
10:('两组均承认活动叠加例外，同时保留券面范围和门槛。',True,True,True,True),
11:('关闭缺预售承诺缺失条款；仍正确否定定金及尾款日推断并请求核实承诺。开启补出人工确认、不自行推算，更完整；按行为等价核实接受关闭回答，不将补证机械计为答对提升。',True,True,True,True),
12:('两组核心结论正确。开启组追加食品安全或二次销售判据，实际返回食品和通用退货片段未明示此判据，严格证据支持记失败；不把一般常识自动视为商城政策。',True,True,True,False),
13:('关闭缺专门卫生用品质量片段，但已返回通用质量与无理由区分条款，足以支持实际结论。保留原必要证据评分为未完整，另做等价证据敏感性分析，不改原标注。',True,True,True,True),
14:('关闭缺试穿区别片段，仍保留期限/完好并使用用户明确磨损事实谨慎核实，未把实际磨损当合理试穿；开启依据更完整，主结论均正确。',True,True,True,True),
15:('关闭把不承诺立即返整券写成整张券不会立即退回，过度确定；开启首句正确，但依据中全退完成后才按券返还也将非保证规则强化成必要条件。两组严格语义均未通过，不隐藏该平局失败。',False,False,False,False),
16:('两组均区分定制履约争议与无理由，并未把退款申请当到账。',True,True,True,True),
17:('两组均按无更优约定的非质量退货判断运费，退款按支付来源、券不折现金；无金额虚构。',True,True,True,True),
18:('两组核心判断均正确，但均在退货资格核实中引入券面条件；本题无优惠券，返回片段也没有以券面决定退货资格的规则，严格依据支持均失败。',True,True,False,False),
19:('两组均未将付款60小时直接当该订单超时；请求核实承诺，无申请或执行保证。',True,True,True,True),
20:('两组均保留退货范围、退款明细和开票方核实，不给金额及唯一红冲流程。',True,True,True,True),
21:('两组均不凭定制/预售标签排除退货。关闭组额外要求核实券面适用范围，与本题及返回证据无关；开启去掉该错位条件。',True,True,False,True),
22:('关闭组谨慎未编造，但遗漏判分卡要求的满减券默认不叠加/活动例外规则，自述证据未说明；开启补齐该规则。记必需点完整性改善，不将谨慎澄清误判成幻觉。',False,True,True,True),
23:('两组均不猜PDF是否加密和密码位数；引用只用来说明现有政策范围，没有将邮箱规则当密码规则。',True,True,True,True),
24:('均不编下载期限/恢复收费，未混同30天申请窗口。开启多余建议提供券面截图，是场景串词，非新政策承诺，保留质量告警。',True,True,True,True),
25:('均不推断有无退货保险及赔付数值；借物流异常说明需核实，没有把运费责任变成保险承诺。',True,True,True,True),
26:('均不编视频平台、时段或预约，没有把处理工作日误当夜间在线时间。',True,True,True,True)
}

MULTI_NOTES={
('027','off',1):('主规则正确，但以运费险作为订单权益例子，当前本轮片段没有该机制，记严格依据不充分；未保证确有保险。',True,False),
('027','off',2):('正确应用新增订单商家承担运费的事实；额外期限和完好要求不在本轮授权复用的单片段中（虽历史有部分相关条款），记证据授权范围风险。',True,False),
('027','on',1):('主规则有据；索取订单号确认免费退货权益，订单工具没有该字段，记能力暗示与多余查单邀请，不记已发生错误工具调用。',True,True),
('027','on',2):('重新检索后正确应用订单明示约定，完整证据支持期限和完好提醒；比关闭多一次检索，但未带来结论改善。',True,True),
('028','off',1):('正确按取消全部关联子单假设说明，但将运费券取消条款并列作为商品满减券依据，记引用适用范围风险。',True,True),
('028','off',2):('更新取消前提成功；全部退货完成后才返券将不承诺立即返还强化成唯一条件，严格语义失败。',False,False),
('028','on',1):('正确保留取消假设及按券规则核实，并提示过期不自动延长；未保证返还。',True,True),
('028','on',2):('更新事实成功，但同样写全部退货完成后才返券，仍有过度必要条件；平局失败。',False,False),
('029','off',1):('普通非质量运费结论正确，但运费险例子没有本轮片段支持，记严格依据不充分。',True,False),
('029','off',2):('按质量争议更新费用判断，未核实责任不保证商家承担；附带食品条件说明与查单邀请冗余。',True,True),
('029','on',1):('按非质量与订单更优约定回答，会员不自动免运费有对应片段支持；查单邀请仍多余。',True,True),
('029','on',2):('质量争议与责任核实判断正确；引用拼成return_return_fresh_policy.txt，随后注明即正确文件名，仍记严格引用错误，不认定为完整有效引用。',True,True),
('030','off',1):('按商品券全部退货后核实规则，没有保证到账；附带关联子单退货判断谨慎。',True,True),
('030','off',2):('按更正后的运费券判断，商品退货不当然返还，未套用商品券结论。',True,True),
('030','on',1):('商品券与有效期规则均有证据支持，无执行承诺。',True,True),
('030','on',2):('主结论不能认定返还正确，但只有在取消时才按券处理为未收录的排他条件；后续允许另有约定也不能消除该绝对化措辞，严格语义退化。',False,False)
}


def citations(answer,evidence):
    names=set(re.findall(r'\b[A-Za-z][A-Za-z0-9_]*\.txt\b',answer))
    sources={r['source'] for r in evidence}
    return {'names':sorted(names),'unavailable':sorted(names-sources),'has_citation':bool(names)}


def main():
    fixed=read('answers-fixed-completed-v1.json')
    dialogues=read('answers-dialogues-v1.json')
    assert fixed['metadata'].get('completed_at') and dialogues['metadata'].get('completed_at')
    retrieval=read('retrieval-v1.json')
    byid={r['id']:r for r in retrieval['rows']}
    review={'method':'Post-run AI-assisted review by the same development assistant, no model judge API. Not independent human/blind ground truth. Numeric grades are provisional and reasoning retained.',
            'source_hashes':{n:sha(n) for n in ['scorecards-30-v1.jsonl','retrieval-v1.json','answers-fixed-v1.json','answers-fixed-completed-v1.json','answers-dialogues-v1.json']},
            'fixed':[],'dialogues':[]}
    for r in fixed['rows']:
        note,po,pn,go,gn=NOTES[int(r['id'][-3:])]
        arms={}
        for arm,passed,grounded in [('off',po,go),('on',pn,gn)]:
            a=r['answers'][arm]
            assert 'answer' in a
            arms[arm]={'scorecard_pass':passed,'grounded':grounded,'wrong_positive_promise':False,
                       'citation_check':citations(a['answer'],byid[r['id']][arm]['evidence'])}
        review['fixed'].append({'id':r['id'],'category':byid[r['id']]['category'],'arms':arms,'reason':note})
    for r in dialogues['rows']:
        turns=[]
        for arm,items in r['arms'].items():
            for i,t in enumerate(items,1):
                note,passed,grounded=MULTI_NOTES[(r['id'][-3:],arm,i)]
                turns.append({'arm':arm,'turn':i,'scorecard_pass':passed,'grounded':grounded,
                              'wrong_positive_promise':False,'citation_check':citations(t['answer'],t['evidence']),
                              'reason':note})
        review['dialogues'].append({'id':r['id'],'turns':turns})
    review['annotation_audit']=[{'id':'ablation-013','issue':'The old annotation requires a dedicated hygiene-quality passage, but return_policy.txt-3 in off evidence also supports the actual quality-vs-no-reason conclusion.',
                                'action':'Keep frozen primary coverage unchanged. Sensitivity: count this alternative once, off complete becomes19/22 instead of18/22. Not an after-the-fact primary relabel.'}]
    save('semantic-review-v1.json',review)
    summary={'method':review['method'],'retrieval':retrieval['summary'],'fixed':{},'dialogues':{},
             'operational_failures':{'original_transport_failures':fixed['original_failure_records'],
                                     'original_first_attempt_successful_responses':51,'planned_successful_fixed_answers':52,
                                     'fixed_request_attempts':53,'unknown_usage_failed_requests':1,
                                     'retry':'One explicit retry after original APIConnectionError. All completed original answers retained.'},
             'annotation_audit':review['annotation_audit']}
    for arm in ['off','on']:
        answers=[r['answers'][arm] for r in fixed['rows']]
        grades=[r['arms'][arm] for r in review['fixed']]
        summary['fixed'][arm]={'n':26,'scorecard_pass':sum(g['scorecard_pass'] for g in grades),
                              'grounded':sum(g['grounded'] for g in grades),
                              'wrong_positive_promise':sum(g['wrong_positive_promise'] for g in grades),
                              'invalid_citation_answers':sum(bool(g['citation_check']['unavailable']) for g in grades),
                              'input_tokens':sum(a['usage'].get('input_tokens',0) for a in answers),
                              'output_tokens':sum(a['usage'].get('output_tokens',0) for a in answers),
                              'total_tokens':sum(a['usage'].get('total_tokens',0) for a in answers),
                              'missing_usage':sum(not a.get('usage') for a in answers),
                              'generation_total_ms':sum(a['generation_ms'] for a in answers),
                              'generation_median_ms':statistics.median(a['generation_ms'] for a in answers),
                              'model_names':sorted({a['model'] for a in answers})}
        ts=[t for r in dialogues['rows'] for t in r['arms'][arm]]
        gs=[t for r in review['dialogues'] for t in r['turns'] if t['arm']==arm]
        modelcalls=[c for t in ts for c in t['answer_model_calls']]
        checkers=[t['policy_check'] for t in ts]
        summary['dialogues'][arm]={'n_dialogues':4,'n_turns':8,'scorecard_pass_turns':sum(t['scorecard_pass'] for t in gs),
                                   'final_turn_pass':sum(t['scorecard_pass'] for t in gs if t['turn']==2),
                                   'grounded_turns':sum(t['grounded'] for t in gs),
                                   'invalid_citation_turns':sum(bool(t['citation_check']['unavailable']) for t in gs),
                                   'checker_calls':sum(c.get('checker_called',False) for c in checkers),
                                   'answer_model_calls':len(modelcalls),'search_calls':sum(len(t['policy_calls']) for t in ts),
                                   'reuse_turns':sum(c.get('mode')=='reuse' for c in checkers),
                                   'checker_errors':sum(c.get('status')=='error' for c in checkers),
                                   'blocked_business_call_turns':sum(bool(t['blocked_business_calls']) for t in ts),
                                   'complete_evidence_turns':sum(t['evidence_grade']['complete'] is True for t in ts),
                                   'total_tokens':sum(c.get('usage',{}).get('total_tokens',0) for c in modelcalls+checkers),
                                   'missing_usage':sum(not c.get('usage') for c in modelcalls+checkers),
                                   'elapsed_total_ms':sum(t['elapsed_ms'] for t in ts)}
    summary['fixed_pairs']={'improved':[r['id'] for r in review['fixed'] if not r['arms']['off']['scorecard_pass'] and r['arms']['on']['scorecard_pass']],
                            'regressed':[r['id'] for r in review['fixed'] if r['arms']['off']['scorecard_pass'] and not r['arms']['on']['scorecard_pass']],
                            'tied':[r['id'] for r in review['fixed'] if r['arms']['off']['scorecard_pass']==r['arms']['on']['scorecard_pass']]}
    summary['no_causal_overclaim']='Different generated wordings in a single sample are not proven caused by extra context, even at temperature=0. No repeated trials or independent blind review.'
    save('summary-v1.json',summary)
    s=summary
    pct=lambda x:f'{x:.1%}'
    token_growth=s['fixed']['on']['total_tokens']/s['fixed']['off']['total_tokens']-1
    text=['# 同源条款补全开启／关闭对照实验','',
          '日期：2026-10-25。已完成冻结题集、真实检索、真实模型生成和逐案复核；未修改应用源码、题集、提示词或检索参数。','',
          '## 结果先看','',
          '**同源补证在本批中改善了必要证据覆盖，但答案改善较小，输入与总Token明显增加。多轮未显示额外结论收益，且存在绝对化措辞和引用错误，不能写成业务准确率100%或成本优化。**','',
          '26道单轮里22道有必要证据标注，证据完整数18→22；4道未知问题不计覆盖分母。52份回答按预写判分卡由同一开发助手辅助复核，严格语义通过24→25，平局失败1题。该评分不是独立人工盲审，需人工确认。','',
          '## 实验条件','',
          '- 冻结源码：`9c6de5ab9642192e5fe604281fa13bacb7d7d441`，知识库18份政策、76片段。',
          '- 30道新增合成题、34轮消息；同一出题助手已知道旧题与系统，不属于独立盲测。已知规则和部分场景重叠逐题披露。',
          '- 26道单轮共享一次检索的同一锚点，关闭只用锚点，开启用锚点及同源补证。原话检索，不增加改写。',
          '- 4段多轮对话两组独立运行冻结LangGraph，底层使用本地政策工具；本次多轮不是MCP传输回归。业务工具均阻断，未触发。',
          '- 相同冻结提示、temperature=0、候选池12、Top3、阈值-2、补证正文预算1800字符，组内交替调用顺序，单次运行。',
          '- 配置模型deepseek-chat，接口返回模型名deepseek-flash；提供方https://api.deepseek.com/v1。无额外模型裁判调用。','',
          '## 单轮检索结果','',
          '| 类型 | 题数 | 关闭完整 | 开启完整 | 改善／退化 |','| --- | ---: | ---: | ---: | --- |']
    for category, r in retrieval['summary'].items():
        if category=='all_answerable':continue
        text.append(f"| {category} | {r['n']} | {r['off_complete'] if r['off_complete'] is not None else '不适用'} | {r['on_complete'] if r['on_complete'] is not None else '不适用'} | {r['improved']}／{r['regressed']} |")
    text += ['','22道可标证据题的完整率为81.8%→100%。平均证据正文265→741字符。补证保留全部锚点，因而基于片段集合的覆盖指标具有单调性；未退化是该指标下的结构性质，不能据此认定生成答案无干扰。','',
             '**标注敏感性：** ablation-013关闭组已返回通用质量条款，虽缺预标的专门卫生用品条款，仍支持实际结论。原标注保持不变；若把通用条款登记为等价证据，关闭完整数为19/22（86.4%），开启仍22/22，提升由18.2个百分点缩为13.6个百分点。不能隐藏这种标注偏差。','',
             '## 单轮回答与成本','',
             '| 指标 | 关闭 | 开启 |','| --- | ---: | ---: |',
             f"| 必需点与语义严格通过（AI辅助复核，暂定） | {s['fixed']['off']['scorecard_pass']}/26 | {s['fixed']['on']['scorecard_pass']}/26 |",
             f"| 严格证据支持（含额外断言） | {s['fixed']['off']['grounded']}/26 | {s['fixed']['on']['grounded']}/26 |",
             '| 原4道未知题未编造所问事实 | 4/4 | 4/4 |',
             f"| 输入Token | {s['fixed']['off']['input_tokens']:,} | {s['fixed']['on']['input_tokens']:,} |",
             f"| 输出Token | {s['fixed']['off']['output_tokens']:,} | {s['fixed']['on']['output_tokens']:,} |",
             f"| 已返回回答报告总Token | {s['fixed']['off']['total_tokens']:,} | {s['fixed']['on']['total_tokens']:,} |",
             f"| 生成耗时合计（成功回答） | {s['fixed']['off']['generation_total_ms']/1000:.2f}s | {s['fixed']['on']['generation_total_ms']/1000:.2f}s |",
             f"| 生成耗时中位数 | {s['fixed']['off']['generation_median_ms']/1000:.2f}s | {s['fixed']['on']['generation_median_ms']/1000:.2f}s |",'',
             f'报告总Token增加{pct(token_growth)}，输入Token增加约39.0%。该成本比较是52次成功回答的已报告用量，不含断连请求未知用量；没有价格换算。生成耗时略增，单次顺序实验受网络、顺序及续跑影响，不构成延迟性能结论。共享检索耗时没有伪装为两组独立测得的端到端时延。','',
             '## 多轮结果单独报告','',
             '| 指标 | 关闭 | 开启 |','| --- | ---: | ---: |']
    for label,key in [('用户轮数','n_turns'),('必要证据完整轮数','complete_evidence_turns'),('严格语义通过轮数（暂定）','scorecard_pass_turns'),('最终轮严格通过（4段）','final_turn_pass'),('严格依据支持轮数','grounded_turns'),('来源名错误轮数','invalid_citation_turns'),('检查模型调用','checker_calls'),('回答/工具选择模型调用','answer_model_calls'),('政策检索次数','search_calls'),('复用证据轮数','reuse_turns'),('报告总Token','total_tokens')]:
        text.append(f"| {label} | {s['dialogues']['off'][key]} | {s['dialogues']['on'][key]} |")
    text += ['',
             '两组8轮均覆盖预标证据、均能更新关键条件，但严格语义判分为关闭7/8、开启6/8；差异来自额外排他性措辞，不是没有理解最新条件。开启组在027第二轮选择重新检索，关闭复用，增加一次检索；不能将检查路径变化归因于必然更好或更差。两组无检查回退、无业务工具调用。','',
             '## 改善、退化与平局案例','',
             '- **补证带来必需规则补齐：022。** 关闭组保守说明无法确认叠加，未编造；开启组补充满减券默认不叠加、活动允许例外，回答更完整。',
             '- **证据补齐但结论原已正确：011、013、014。** 新片段提升可追溯完整性，不能把这三题都计算成答案由错变对；013还暴露等价证据漏标。',
             '- **平局失败：015。** 关闭将不承诺立即返券写成不会立即返券；开启虽首句保守，依据仍出现全部退完后才返券的绝对化。原文不支持将例外与可能性改为排他条件。',
             '- **严格依据一退一进：012、021。** 开启012追加原片段未明示的二次销售判据；关闭021把券面条件串到退货资格，开启未出现。两组主结论都正确，按证据支持单独记分。',
             '- **仍有共同干扰：018。** 两组都把券面引入预售商品退货资格判断，说明已有回答约束未完全避免场景串词。',
             '- **多轮语义退化候选：030最终轮。** 开启增加只有取消才按券处理的排他条件；关闭只说商品退货不当然返还，边界更准确。单次措辞差异不能证明由补证必然导致。',
             '- **引用错误：029开启最终轮。** 生成了return_return_fresh_policy.txt，虽随后解释即正确文件名，严格来源校验仍应标错。',
             '- **无答案平局：023～026。** 两组均不猜密码、下载期限、保险或排班；开启024多余提出券面截图，记冗余，不把未编造未知事实夸成全回答完美。','',
             '## 逐案复核（全量，不挑选）','',
             '| ID | 类型 | 严格语义 关/开 | 严格依据 关/开 | 复核理由 |','| --- | --- | --- | --- | --- |']
    mark=lambda b:'通过' if b else '未通过'
    for r in review['fixed']:
        a,b=r['arms']['off'],r['arms']['on']
        text.append(f"| {r['id']} | {r['category']} | {mark(a['scorecard_pass'])}/{mark(b['scorecard_pass'])} | {mark(a['grounded'])}/{mark(b['grounded'])} | {r['reason']} |")
    text += ['','### 多轮逐轮复核','', '| ID | 组 | 轮 | 严格语义 | 严格依据 | 说明 |','| --- | --- | ---: | --- | --- | --- |']
    for r in review['dialogues']:
        for t in r['turns']:
            text.append(f"| {r['id']} | {t['arm']} | {t['turn']} | {mark(t['scorecard_pass'])} | {mark(t['grounded'])} | {t['reason']} |")
    text += ['','## 失败与续跑审计','',
             '原运行在007关闭组遇到APIConnectionError，底层连接在响应前断开。原answers-fixed-v1.json不覆盖；resume_fixed_v1.py仅显式补测该失败项一次并完成尚未运行项，已成功的12份回答原样保留。',
             '固定题共53次请求尝试，返回52份可复核回答，另1次断连没有用量回执。原始首尝试有效回答51/52；补测后的语义表属于完成配对的补充分析，不抹除运行失败。多轮共32次成功模型调用（16次检查、16次回答）。全实验可计报告用量150,334 Token，另有1次未知用量，不能写精确账单总量。','',
             '## 可用于简历的保守表述','',
             '> 冻结版本并构造30道合成题开展同源补证消融；单轮22道有依据题必要证据完整数由18提升至22，复核等价证据后为19至22，同时记录Token增幅、回答绝对化与引用错误，交付配对轨迹和复现实验。','',
             '篇幅有限时可只写“补充未用于调参的合成题消融，对照证据完整性、回答质量与成本，记录收益及副作用”，不把同一助手的语义评分包装成独立业务准确率。','',
             '## 局限与下一步','',
             '- 数据是同一助手辅助构造、来自相同演示政策，并非真实客服分布；部分题复用旧规则与场景。',
             '- 逐案语义分数由同一开发助手复核，属于暂定判断。严格依据及“才/只有”判定需独立人工审阅；原始答案与理由均保留。',
             '- 冻结原标注不根据输出更改。等价证据013只做敏感性分析，正式新版本标注应另存并保留审计。',
             '- 覆盖增加具有集合单调性，必须与回答副作用、输入长度同时解释；单次temperature=0不等于确定性因果证据。',
             '- 无新生产功能、无HTTP/浏览器或并发压测，本次多轮也不经MCP传输。',
             '- 本轮没有按失败修改系统。若之后据此修复，这30题就成为已知回归集，再验证需另准备新题。','',
             '## 文件','',
             '- [判分卡](30题判分卡-v1.md)／[JSONL题集](scorecards-30-v1.jsonl)',
             '- [预注册协议](protocol-30-v1.json)／[运行锁](execution-lock-v1.json)',
             '- [单轮检索](retrieval-v1.json)／[原始失败批](answers-fixed-v1.json)／[续跑完成批](answers-fixed-completed-v1.json)',
             '- [多轮原始轨迹](answers-dialogues-v1.json)／[逐案复核](semantic-review-v1.json)／[指标汇总](summary-v1.json)',
             '- [执行器](run_ablation_v1.py)／[显式续跑器](resume_fixed_v1.py)／[报告生成](review_results_v1.py)','']
    (P/'同源补证消融实验报告-v1.md').write_text('\n'.join(text),encoding='utf-8')
    print(json.dumps({'fixed':summary['fixed'],'dialogues':summary['dialogues'],'paired_fixed':summary['fixed_pairs']},ensure_ascii=True,indent=2))


if __name__=='__main__': main()
