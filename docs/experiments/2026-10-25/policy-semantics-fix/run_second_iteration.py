"""Second iteration kept separate; first iteration artifacts are immutable."""
import argparse,asyncio,hashlib,json,sys
from pathlib import Path
import evaluate as runner

P=Path(__file__).resolve().parent
V2=P/'iteration-2'


def prepare():
 if V2.exists():raise FileExistsError(V2)
 V2.mkdir()
 for name in ['answer_policy.before.py','acceptance-lock.json']:
  (V2/name).write_bytes((P/name).read_bytes())
 source={}
 for path in (runner.ROOT/'data/knowledge').glob('*.txt'):
  lines=[x.strip() for x in path.read_text(encoding='utf-8-sig').splitlines() if x.strip()]
  for i,line in enumerate(lines[1:]):source[f'{path.name}-{i}']={'id':f'{path.name}-{i}','source':path.name,'content':lines[0]+'\n\n'+line}
 inputs=[
  ('fresh-01','uncertain','这包卫生用品只知道品类名称，我没提供密封状态或购买时确认。客服说这类商品全部禁止七日无理由，不必再核实。这能作为商城的统一规定吗？',['return_hygiene_policy.txt-0'],'不能按品类一律排除，需核实拆封对安全健康品质影响与购买确认。'),
  ('fresh-02','uncertain','订单里的预售发货日期没有记录，客服说为了方便就从定金日加48小时算最迟发货。既然缺日期，能这样自行补一个保证期限吗？',['presale_shipping_policy.txt-0','presale_shipping_policy.txt-2'],'按预售确认承诺，缺失联系人工确认，不自行推算或套用现货48小时。'),
  ('fresh-03','uncertain','我的券是否返还仍待核实，客服先建议我等处理结果。我能不能反过来理解成系统已经确定这张券永远不会返还？',['coupon_refund_policy.txt-1','coupon_refund_policy.txt-4'],'待核实和不承诺立即不意味着永远不返，应核实对应券与活动规则；不保证必返。'),
  ('fresh-04','explicit','我是普通会员，商品没有质量问题，已超过七天且没有订单延长权益。只问是否能自动获得无理由退货资格，别把我当黑金会员。',['return_policy.txt-1'],'普通会员超过7天不自动获得无理由资格，不弱化成自动延长。'),
  ('fresh-05','explicit','商家已核实这件物品确实按我个人指定的图案制作，我也确认交付完全符合约定、没有质量问题。只因不喜欢就想按通常七日无理由退，政策是否明确排除？',['return_custom_policy.txt-0','return_custom_policy.txt-1'],'真实个性化定作不适用通常七日无理由；不能扩大排除履约争议。'),
  ('fresh-06','explicit','这张已返还的优惠券确实过期了，没有额外延期约定。我只想知道返还动作本身会不会自动延长它的有效期。',['coupon_refund_policy.txt-3'],'返还不自动延期，过期不能继续用；不保证补发。')
 ]
 cases=[{'id':key,'group':group,'question':q,'evidence':[source[x] for x in ids],
         'expected':expected,'note':'在iteration-1完成并按已知015/028修订后，运行前准备；新增定向合成题，规则仍为已知政策，不是独立盲测。'}
        for key,group,q,ids,expected in inputs]
 runner.HERE=V2
 runner.save('acceptance-cases.json',cases)
 lock=runner.read('acceptance-lock.json')
 lock.update({'cases_sha256':runner.sha(V2/'acceptance-cases.json'),'cases':6,'uncertain':3,'explicit':3,
              'iteration':'2','parent_acceptance_lock_sha256':runner.sha(P/'acceptance-lock.json')})
 runner.save('acceptance-lock.json',lock)
 runner.freeze()
 # Clarify the plan before requests; frozen runner and prompts remain unchanged.
 e=runner.read('execution-lock.json')
 e['plan']='8 fresh baseline-vs-final fixed-evidence pairs (6 new directed cases,2 known);26 old fixed after-only regressions;4 old dialogues after-only. First iteration 12 acceptance cases are now known and are not reused as fresh tests.'
 e['iteration']='2'
 e['previous_iteration_artifacts']={name:runner.sha(P/name) for name in ['answers.json','dialogues.json','answer_policy.after.py']}
 e['limitations']='Not independent blind review. Directed new cases after generic repair strategy; known cases remain regression. Single sample, no extra judge model.'
 runner.save('execution-lock.json',e)
 print('Iteration 2 locked: 6 new directed cases + 2 known pairs; historical regression remains26+4 dialogues.',flush=True)


if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','answers','dialogues']);args=p.parse_args()
 if args.mode=='prepare':prepare()
 else:
  runner.HERE=V2
  if args.mode=='answers':asyncio.run(runner.answers())
  else:asyncio.run(runner.dialogues())
