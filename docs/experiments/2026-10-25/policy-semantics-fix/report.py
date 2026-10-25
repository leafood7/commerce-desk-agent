"""Record scoped post-run review and costs. Does not call models or change cases."""
import hashlib,json,re,statistics
from pathlib import Path
P=Path(__file__).resolve().parent
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')

def costs(calls):
 return {'calls':len(calls),'errors':sum('error_type' in x for x in calls),
         'missing_usage':sum(not x.get('usage') for x in calls),
         'input_tokens':sum(x.get('usage',{}).get('input_tokens',0) for x in calls),
         'output_tokens':sum(x.get('usage',{}).get('output_tokens',0) for x in calls),
         'total_tokens':sum(x.get('usage',{}).get('total_tokens',0) for x in calls),
         'median_generation_ms':statistics.median(x['generation_ms'] for x in calls) if calls and all('generation_ms' in x for x in calls) else None}

NOTES={
1:'邮件排查顺序保留，未承诺重新开票或发送。',
2:'不将其他平台会员权益及年费迁移成本商城。',
3:'个人改企业仍需核实，不承诺无条件变更。',
4:'未凭订单总额保证满减可用；多余核实系统金额认定但无错误肯定。',
5:'运费同票问题保留开票方核实。',
6:'不承诺活动专享券必返；普通规则不适用于活动券的概括仍略宽，应按特别规则核实而非全盘排除。',
7:'7日期限与工作日参考区间区分，未允许阶段累加；末句同时核查发起和到账，未只用发起替代返还。',
8:'自助窗口不等于永久失权，服务目标不等于无条件保证。',
9:'主结论正确，仍将普通商品券部分退货条款用于运费券解释，跨券种套用风险未修复。',
10:'允许叠加的活动仍需满足单券范围，没有弱化明确限制。',
11:'不把定金或尾款日当发货期限，缺承诺要求人工核实。',
12:'非生鲜不自动符合完好，必要密封破坏只记可能影响；引用为政策标题而非要求的来源文件名。',
13:'质量售后与无理由分开，未以例外排除全部售后。',
14:'尺码理由不覆盖期限与完好，使用痕迹仍需结合状态核实。',
15:'已确认错误未再出现：主结论不承诺立即返还，依据没有增加全退后才返的必要条件。',
16:'定制履约争议单独核实，申请不等于到账。',
17:'运费责任与支付来源正确，未把券折成现金；只用政策名称/条号，没有给来源文件名。',
18:'预售与加急主结论正确，但仍把券面引入退货资格，场景串词未修复。',
19:'60小时不能直接代替订单承诺，保留资格核实。',
20:'发票可能调整，不一律指定红冲重开。',
21:'不凭标签否定退货，本次没有增加只有个性化定制才不适用的全局排他条件。',
22:'未知券种不保证使用，解释默认叠加规则；该项属于完整性检查，不估计准确率改善。',
23:'不猜PDF密码及是否加密。',
24:'不编下载期限或恢复收费。',
25:'不编保险、保额或赔付比例。',
26:'不编视频平台与在线时段。'
}

def main():
 summary={'method':'Same assistant post-run targeted semantic review. Not independent human/blind scoring; no claim of business accuracy.',
          'scope':'Only shared ANSWER_GUIDANCE changed. No postprocessor, lexical ban, new model node, retrieval change or citation gate.',
          'iterations':{},'offline':{'iteration1':{'tests':107,'passed':107,'seconds':8.176},
                                   'iteration2':{'tests':107,'passed':107,'seconds':8.716},
                                   'initial_sandbox_attempt':'Stopped after a stall; rerun with fake models and external tracing disabled passed. No silent test failure counted as pass.'}}
 review={'criteria':'Whether policy certainty, condition direction and exceptions are preserved; explicit valid prohibitions remain explicit. Other defects separately retained.',
         'reviewer':'AI assistant; project-owner confirmations only apply to earlier015/030/013/022, not to these newly generated outputs.',
         'iterations':{},'final_regression':[],'final_dialogues':[]}
 total=0
 for label,path in [('iteration1',P),('iteration2',P/'iteration-2')]:
  a=read(path/'answers.json');d=read(path/'dialogues.json')
  assert a['metadata'].get('completed_at') and d['metadata'].get('completed_at')
  arms={arm:costs([r['answers'][arm] for r in a['paired']]) for arm in ['before','after']}
  reg=costs(a['regression'])
  turns=[t for r in d['rows'] for t in r['turns']]
  modelcalls=[c for t in turns for c in t['answer_model_calls']]
  checks=[t['policy_check'] for t in turns]
  dc=costs(modelcalls+checks)
  dc.update({'turns':len(turns),'policy_calls':sum(len(t['policy_calls']) for t in turns),
             'reuse_turns':sum(c['mode']=='reuse' for c in checks),
             'checker_errors':sum(c.get('status')=='error' for c in checks),
             'blocked_business_calls':sum(len(t['blocked_business_calls']) for t in turns)})
  info={'paired':arms,'fixed_regression':reg,'dialogues':dc,
        'source_hashes':{name:sha(path/name) for name in ['answers.json','dialogues.json','execution-lock.json','paired-cases.json','answer_policy.after.py']}}
  info['total_reported_tokens']=sum(x['total_tokens'] for x in [*arms.values(),reg,dc])
  info['total_model_calls']=sum(x['calls'] for x in [*arms.values(),reg,dc])
  total+=info['total_reported_tokens'];summary['iterations'][label]=info
  records=[]
  for r in a['paired']:
   known15=r['id']=='known-015'
   # Read as targeted preservation checks, not overall answer grades.
   before_pass=not known15
   after_pass=not known15 if label=='iteration1' else True
   records.append({'id':r['id'],'before_target_pass':before_pass,'after_target_pass':after_pass,
                    'reason':('首轮前后均出现全退后才按规则返券。' if known15 and label=='iteration1' else
                              '最终版短引原句，移除新增必要条件；新鲜配对基线仍复现才字强化。' if known15 else
                              '两组均保留该题所需的非保证或明确限制；不能据此宣称新版在新题更优。'),
                    'additional_flags':(['第一轮modal-09两组都在否定转让后又提出朋友订单按券面核实，额外边界不严谨，目标禁止句正确不代表全回答通过。'] if r['id']=='modal-09' else [])})
  review['iterations'][label]=records
  info['targeted_pairs']={'n':len(records),'before_pass':sum(r['before_target_pass'] for r in records),'after_pass':sum(r['after_target_pass'] for r in records),
                           'new_directed_n':sum(not r['id'].startswith('known') for r in records),
                           'interpretation':'Targeted modality checks only; not overall semantic pass or accuracy.'}
  if label=='iteration2':
   for r in a['regression']:
    assert 'answer' in r
    names=set(re.findall(r'\b[A-Za-z][A-Za-z0-9_]*\.txt\b',r['answer']))
    review['final_regression'].append({'id':r['id'],'targeted_modality_pass':True,'note':NOTES[int(r['id'][-3:])],
                                       'has_source_filename':bool(names),'overall_semantic_pass':'not asserted'})
   for r in d['rows']:
    for i,t in enumerate(r['turns'],1):
     review['final_dialogues'].append({'id':r['id'],'turn':i,'targeted_modality_pass':True,
                                       'note':('027首轮仍邀请订单号确认运费权益，现有工具无该字段；不属于本次模态修复。' if r['id']=='ablation-027' and i==1 else
                                               '028最终轮已保留全退后按规则处理，没有新增才/只有。' if r['id']=='ablation-028' and i==2 else
                                               '030最终轮仍说商品退货不当然返还，未增加仅取消可处理。' if r['id']=='ablation-030' and i==2 else
                                               '本轮未观察到目标模态强化或明确限制弱化；仍不视为全维度无错误。')})
 summary['all_iterations_reported_tokens']=total
 summary['all_iterations_model_calls']=sum(i['total_model_calls'] for i in summary['iterations'].values())
 save(P/'verification-summary.json',summary)
 save(P/'post-run-review.json',review)
 v=summary['iterations']['iteration2']
 growth=v['paired']['after']['total_tokens']/v['paired']['before']['total_tokens']-1
 text=['# 政策语义修复与验证报告','',
       '日期：2026-10-25。范围：只修改共享回答约束，保留原文的确定程度、条件方向和例外。引用校验、查询改写、检索与审批流程未改；未更新简历，未提交或推送。','',
       '## 最终变更','',
       '`src/agents/answer_policy.py`增加7行约束：区分非保证与确定否定，避免把条件处理反推成唯一处理条件，保留真正明确的禁止；解释部分可短引原句，增加一个已知返券条款的正确/错误改述示例。仍在同一次生成中应用，无新增模型调用节点。','',
       '这是一项提示约束改造，不是形式化逻辑验证，也不保证未来所有回答正确。没有使用禁词替换，“只有/必须/不能”在原文有依据时仍允许出现。','',
       '## 为什么保留两轮','',
       '- 首轮只有一般性约束，已确认015仍在依据部分生成“全退后才返”，多轮028也仍复现。首轮没有算作修复成功。',
       '- 第二轮补充短引原句与一个局部示例后，015固定证据配对、015旧题回归和028多轮最终轮均未再出现该强化。030多轮未新增“只有取消才处理”。',
       '- 首轮12道定向题已成为已知验收题；第二轮另准备6道定向题并运行前冻结。它们来自相同政策、由同一助手构造，仍不属于独立盲测。',
       '- 原始题集、标注、第一轮失败及全部回答均保留。全程没有依据结果修改检索或业务逻辑。','',
       '## 验证结果','',
       '| 项目 | 结果 |','| --- | --- |',
       '| 最终版离线回归 | 107项通过；前一轮也是107项通过 |',
       '| 第一轮目标模态配对 | 13/14→13/14；已知015前后均失败 |',
       '| 第二轮目标模态配对 | 7/8→8/8；新增6题两组均通过，改善来自已知015 |',
       '| 最终版26道旧单轮回归 | 本次模态目标未发现失败；不是全维度26/26准确率 |',
       '| 最终版4段、8轮对话 | 目标条件强化未再观察到；本地工具，非MCP传输回归 |',
       '| 业务调用／检查回退 | 两轮均0／0 |',
       '| 配置模型／接口返回模型名 | deepseek-chat／deepseek-flash |','',
       '**评分限制：** 定向模态判定和回归复核由同一AI助手完成，未经新的独立人工盲审。它们只说明本批目标约束表现，不代表全回答正确率、线上准确率或统计显著收益。单次temperature=0仍有波动。','',
       '## 对照的边界','',
       '- 新增及已知定向配对：相同原文证据、模型、温度和基础模板，只变更共享回答约束，交替顺序。已知030的固定题只验证其最终轮证据与问题，不代替多轮测试。',
       '- 26道旧题是新提示的固定证据回归，与原始消融的生成结果作历史对比，不把它称为同期配对因果实验。',
       '- 4段对话使用冻结的原聊天图、同一政策检索及最终共享约束，开启同源补证。检查器与工具节点不变；新回答会改变历史与后续路径，所以不强行固定查询。','',
       '## 成本','',
       '| 第二轮8组配对 | 修改前 | 最终版 |','| --- | ---: | ---: |',
       f"| 模型调用 | {v['paired']['before']['calls']} | {v['paired']['after']['calls']} |",
       f"| 输入Token | {v['paired']['before']['input_tokens']} | {v['paired']['after']['input_tokens']} |",
       f"| 报告总Token | {v['paired']['before']['total_tokens']} | {v['paired']['after']['total_tokens']} |",
       f"| 生成耗时中位数 | {v['paired']['before']['median_generation_ms']/1000:.2f}s | {v['paired']['after']['median_generation_ms']/1000:.2f}s |",'',
       f'配对报告Token增加约{growth:.1%}，来自更长提示及回答变化。模型调用次数没增加不等于Token成本没增加。小样本中位耗时不能用于宣称提速。',
       f"两轮实验合计{summary['all_iterations_model_calls']}次模型调用，接口报告{total:,} Token；本轮无接口错误和缺失用量，未换算账单金额。无额外模型裁判调用。",'',
       '## 仍有的问题，未隐藏','',
       '- 旧题018仍把“券面”串入预售退货资格，属于场景与证据适用范围问题，本轮不扩大修改。',
       '- 多轮027仍邀请提供订单号确认运费权益，但现有订单工具没有该字段；这不是本轮已解决的能力边界问题。',
       '- 部分回答仅引用政策标题或条号，没有给要求的来源文件名；未新增运行时引用校验。',
       '- 旧题009仍有普通商品券部分退货规则被用于运费券解释的风险；核心结论未作返还保证，但完整证据适用性并非完美。',
       '- 第二轮对已知015使用了针对性示例，已知题改善不能作为未见场景泛化证明。新增定向题两组原本均能答对，也不能夸成新题准确率提升。','',
       '## 最终26题回归逐案记录','',
       '| 案例 | 本轮目标检查 | 备注 |','| --- | --- | --- |']
 for r in review['final_regression']:text.append(f"| {r['id']} | 未观察到目标模态失败 | {r['note']} |")
 text+=['','## 多轮逐轮记录','', '| 案例 | 轮 | 备注 |','| --- | ---: | --- |']
 for r in review['final_dialogues']:text.append(f"| {r['id']} | {r['turn']} | {r['note']} |")
 text+=['','## 复现与审计','',
        '- [汇总指标](verification-summary.json)／[逐项AI辅助复核](post-run-review.json)',
        '- [修改前备份](answer_policy.before.py)／[第一轮候选](answer_policy.after.py)／[最终候选](iteration-2/answer_policy.after.py)',
        '- [首轮题集锁](acceptance-lock.json)／[第二轮运行锁](iteration-2/execution-lock.json)',
        '- [第一轮固定回答](answers.json)／[第一轮多轮轨迹](dialogues.json)',
        '- [第二轮固定回答](iteration-2/answers.json)／[第二轮多轮轨迹](iteration-2/dialogues.json)',
        '- [准备脚本](prepare.py)／[评测执行器](evaluate.py)／[第二轮执行入口](run_second_iteration.py)',
        '- 现有产物不覆盖；复现应使用新输出目录并冻结配置，不能直接覆盖已有结果。',
        '- 本轮最初沙箱离线运行停滞后中止；关闭外部追踪并在正常执行权限下重跑通过。预期错误分支日志和Pydantic告警不等于测试失败。','']
 (P/'政策语义修复验证报告.md').write_text('\n'.join(text),encoding='utf-8')
 print(json.dumps({'calls':summary['all_iterations_model_calls'],'tokens':total,'iteration2':v},ensure_ascii=True,indent=2))

if __name__=='__main__':main()
