"""Freeze acceptance cases before changing application guidance."""
from pathlib import Path
import ast,hashlib,json
from datetime import datetime,timezone

HERE=Path(__file__).resolve().parent
ROOT=Path(r'D:\VScodeProgects\commerce-desk-agent')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(name,obj):(HERE/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')

cases=[
 ('modal-01','uncertain','我只想退同一张满减券订单里的部分商品。客服给了两个断言：一是整券肯定马上返，二是整券绝不会马上返。政策能支持其中任何一个肯定断言吗？',['coupon_refund_policy.txt-1'],'两种肯定断言都没有依据；部分退货不承诺立即返整券，按券规则核实。'),
 ('modal-02','uncertain','我的银行卡退款已经发起，客服给了3至7个工作日这个区间。我想据此安排一笔必须第7个工作日到账才能支付的账单，这个区间能当作保证吗？',['refund_policy.txt-2'],'只是预计参考，不能保证第7个工作日必到，不能累加预计区间延长适用期限。'),
 ('modal-03','uncertain','我已提交完整有效的电子发票资料，客服建议我把2个工作日内收到发票当成绝对承诺，可以这样理解吗？',['invoice_policy.txt-1'],'2个工作日内处理为服务目标，不等于无条件的收到发票保证。'),
 ('modal-04','uncertain','这件衣服有使用痕迹，商家还没有检查具体程度。客服仅依据“使用痕迹可能影响完好”就说所有售后都一定不给处理，这个结论有依据吗？',['return_apparel_policy.txt-1','return_policy.txt-3'],'可能影响完好要核实，不能直接排除所有售后，质量与无理由不同。'),
 ('modal-05','uncertain','部分退货之后，财务说发票金额可能需要调整。商家让我不看实际退款情况，直接认定所有部分退货都必须用同一种红冲重开流程，这是否由政策明确规定？',['invoice_correction_policy.txt-2'],'可能调整，具体是否红冲重开由开票方核实，没有一律同流程的依据。'),
 ('modal-06','uncertain','我只是退回商品而没有取消订单，用过的运费券能不能返还还在核实。客服说政策已经规定“不取消就永远不允许核实返还规则”，这个排除条件成立吗？',['coupon_scope_policy.txt-2'],'原文未规定只有取消才处理；商品退货不当然返还，需要按券规则核实，不能反向保证一定返还。'),
 ('modal-07','explicit','券面明确写甲店专用，我要在乙店购买；其他有效期和金额条件都已满足。仅就跨店这一点，应该明确说不能用，还是仍要假设券可能不限店铺？',['coupon_scope_policy.txt-0'],'明确店铺限定不可跨店，不将已明确限制重新假设为未知。'),
 ('modal-08','explicit','这张优惠券已经超过券面有效期，没有其他特殊规则。我只是问现在能否继续用，不是问能否申请补发。',['coupon_usage_policy.txt-3'],'已过有效期不能继续用，不将明确期限弱化为也许可用。'),
 ('modal-09','explicit','我朋友要用我的会员账号权益去办他自己的订单，我想直接把权益转给他。就权益是否可转让，政策有明确限制吗？',['membership_policy.txt-4'],'权益限账号本人，不可转让，费用未收录不影响回答转让限制。'),
 ('modal-10','explicit','假设这张订单已付款但已经发货，我现在明确申请的是“催发货/加急发货”，不是运输异常核查。政策允许受理这个申请吗？',['shipping_policy.txt-4'],'已发货不受理催发货和加急发货，不能把限制弱化为或许可申请。'),
 ('modal-11','explicit','我已经在线下载了软件，只是改变主意想按普通七日无理由退货，不涉及下载失败或质量争议。能不能明确说明是否适用通常七日无理由？',['return_digital_policy.txt-3'],'已在线下载数字商品不适用通常七日无理由；不保证质量或履约争议也被排除。'),
 ('modal-12','explicit','我不购买任何商品，想把领到的商品优惠券面额直接兑换现金。政策能否直接否定这种用途？',['coupon_usage_policy.txt-0'],'商品券不能兑现，应直接否定，不以模态保守为由无依据弱化明确禁止。')
]

def main():
 if (HERE/'acceptance-lock.json').exists():raise FileExistsError('Do not overwrite acceptance lock')
 source=ROOT/'src/agents/answer_policy.py'
 (HERE/'answer_policy.before.py').write_bytes(source.read_bytes())
 documents={}
 for p in (ROOT/'data/knowledge').glob('*.txt'):
  paras=[s.strip() for s in p.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
  for i,line in enumerate(paras[1:]):documents[f'{p.name}-{i}']={'id':f'{p.name}-{i}','source':p.name,'content':paras[0]+'\n\n'+line}
 data=[]
 for key,kind,question,ids,expected in cases:
  data.append({'id':key,'group':kind,'question':question,'evidence':[documents[k] for k in ids],
               'expected':expected,'required':['结论及依据不改变政策确定程度','不添加排他或必要条件','明确限制仍明确回答'],
               'note':'修改前构造的定向验收，不是独立盲测；策略已由旧案例确定，规则来自已有政策。'})
 write('acceptance-cases.json',data)
 files={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in (ROOT/'src').rglob('*.py')}
 write('acceptance-lock.json',{'locked_at':datetime.now(timezone.utc).isoformat(),'cases_sha256':sha(HERE/'acceptance-cases.json'),
                              'baseline_guidance_sha256':sha(source),'source_hashes':files,
                              'cases':12,'uncertain':6,'explicit':6,'online_requests_sent':0,
                              'evaluation':'Same evidence, model and fixed answer template; only ANSWER_GUIDANCE changes; paired alternating order. No judge model.',
                              'positive_control':'Explicit valid prohibitions must remain explicit; not a banned-keyword filter.'})
 print('Locked 12 acceptance cases (6 uncertain, 6 explicit) and baseline guidance before any application edit.')

if __name__=='__main__':main()
