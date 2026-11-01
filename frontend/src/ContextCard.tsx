type Field = {value:unknown;source:string};
type Context = {active_order?:string;version:number;latest_conditions:Record<string,Field>;history_window_truncated:boolean;summary_items:number;current_unknown_fields:string[];model_budget?:{status:string}};
const names:Record<string,string>={order_number:"订单",return_item_id:"商品行",return_policy_id:"政策规则",return_quantity:"数量",return_condition:"商品状态",return_basis:"退货分支",reason:"原因",shipping_kind:"催单类型"};
const values:Record<string,string>={used:"已使用",unused:"未使用",unknown:"待核实",quality_issue:"质量问题退货",no_reason:"无理由退货",normal:"普通",expedite:"加急"};
export default function ContextCard({context}:{context?:Context}){
  if(!context)return null;
  return <details className="document-card"><summary>最近核对的信息 · {context.active_order||"尚未关联订单"}</summary>
    <p>以下是最近一次核对的条件，修改后需重新核对；订单事实、质检及审批仍以实际业务记录为准。</p>
    <dl>{Object.entries(context.latest_conditions).filter(([key,field])=>names[key]&&field.value!=null).map(([key,field])=><div key={key}><dt>{names[key]}</dt><dd>{values[String(field.value)]||String(field.value)}</dd></div>)}</dl>
    {context.model_budget?.status==="required_input_too_large"&&<p>完整必要信息超过模型输入上限，本次改用规则核对；关键条件未被截短。</p>}
    <p className="muted">{context.history_window_truncated?"较早历史已省略，完整记录仍可在会话记录查看。":"历史按本次订单范围整理。"}历史摘要不作为政策依据或审批凭据。</p>
  </details>;
}
