import {test,expect,type Page} from "@playwright/test";
import {readFileSync} from "node:fs";

const fixture=JSON.parse(readFileSync(new URL("../../work/returns-v1/demo.json",import.meta.url),"utf-8"));
const password=()=>process.env.WORKBENCH_TEST_PASSWORD||JSON.parse(readFileSync(new URL("../../work/fullstack/local-login.json",import.meta.url),"utf-8")).password;
async function login(page:Page,role="support"){
  await page.goto("/");await page.getByLabel("邮箱").fill(role+"@demo.test");await page.getByLabel("密码").fill(password());
  await page.getByRole("button",{name:"进入工作台"}).click();await page.getByRole("button",{name:"退货申请",exact:true}).click();
}

test("return agent asks, corrects, persists, approves and simulates",async({page,browser})=>{
  test.setTimeout(120_000);
  await login(page);
  await page.getByLabel("退货订单号").fill(fixture.orders.normal.number);
  await page.getByRole("button",{name:"读取退货订单"}).click();
  await page.getByLabel("退货规则",{exact:true}).selectOption(fixture.policy_id);
  await page.getByLabel("退货原因").fill("浏览器退货链路验收");
  await page.getByRole("button",{name:"启动退货 Agent 核对"}).click();
  await expect(page.getByRole("status")).toContainText("请核实商品是否使用",{timeout:40_000});
  await page.getByLabel("商品状态",{exact:true}).selectOption("unused");
  await page.getByRole("button",{name:"补充退货条件"}).click();
  await expect(page.getByRole("status")).toContainText("请确认本版本退货申请",{timeout:40_000});
  // Edits must be checked before confirming; revert by explicit recheck.
  await page.getByLabel("退货原因").fill("浏览器更正后退货原因");
  await expect(page.getByRole("button",{name:"确认已核对的退货预览"})).toBeDisabled();
  await page.getByRole("button",{name:"按当前信息重新核对"}).click();
  await expect(page.getByRole("button",{name:"确认已核对的退货预览"})).toBeEnabled({timeout:40_000});
  const id=await page.getByLabel("恢复退货流程").inputValue();
  await page.reload();await login(page);
  await page.getByLabel("恢复退货流程").selectOption(id);
  await expect(page.getByRole("status")).toContainText("请确认本版本退货申请");
  await page.getByRole("button",{name:"确认已核对的退货预览"}).click();
  await expect(page.getByRole("status")).toContainText("已提交独立审批",{timeout:40_000});
  const reviewerContext=await browser.newContext({baseURL:process.env.WORKBENCH_WEB_URL||"http://127.0.0.1:5173"});
  try{
    const reviewer=await reviewerContext.newPage();await login(reviewer,"approver");
    await reviewer.getByRole("button",{name:new RegExp(fixture.orders.normal.number+" · 待审批")}).first().click();
    await expect(reviewer.getByText("实际退款金额：未确认。",{exact:false})).toBeVisible();
    await reviewer.getByRole("button",{name:"批准退货申请"}).click();
    await page.getByRole("button",{name:"刷新退货审批结果"}).click();
    await expect(page.getByRole("button",{name:"确认模拟受理"})).toBeVisible({timeout:40_000});
    await page.getByRole("button",{name:"确认模拟受理"}).click();
    await expect(page.getByRole("heading",{name:"当前申请：模拟已受理"})).toBeVisible({timeout:40_000});
    await expect(page.getByRole("status")).toContainText("真实退款未执行");
    await page.getByRole("button",{name:"会话记录",exact:true}).click();
    const selector=page.getByLabel("选择会话");
    const options=await selector.locator("option").allTextContents();
    const position=options.findIndex(t=>t.includes("申请退货"));
    expect(position).toBeGreaterThan(0);
    await selector.selectOption({index:position});
    await page.getByText("该会话的近期流程",{exact:true}).click();
    await page.getByRole("button",{name:"打开售后流程 "+id.slice(0,8)}).click();
    await expect(page.getByRole("heading",{name:"退货申请",exact:true})).toBeVisible();
    await expect(page.getByRole("status")).toContainText("真实退款未执行");
  }finally{await reviewerContext.close();}
});

test("return UI does not infer receipt from estimated arrival",async({page})=>{
  await login(page);await page.getByLabel("退货订单号").fill(fixture.orders.missing_receipt.number);
  await page.getByRole("button",{name:"读取退货订单"}).click();
  await expect(page.getByText("实际签收：未提供，不能使用预计送达代替")).toBeVisible();
  await page.getByLabel("退货规则",{exact:true}).selectOption(fixture.policy_id);
  await page.getByLabel("退货原因").fill("缺少签收字段验收");await page.getByLabel("商品状态",{exact:true}).selectOption("unused");
  await page.getByRole("button",{name:"保存退货草稿"}).click();
  await expect(page.getByText("待核实：实际签收时间（须由业务数据核实）")).toBeVisible();
  await expect(page.getByRole("button",{name:"确认本版本并提交审批"})).toBeDisabled();
});
