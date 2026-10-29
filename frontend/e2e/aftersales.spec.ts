import {test,expect as baseExpect,type Page} from "@playwright/test";
import {readFileSync} from "node:fs";
import {fileURLToPath} from "node:url";
const expect=baseExpect.configure({timeout:20_000});
test.describe.configure({timeout:90_000});
const orders=JSON.parse(readFileSync(new URL("../../work/fullstack/aftersales-demo.json",import.meta.url),"utf-8"));
function password(){return process.env.WORKBENCH_TEST_PASSWORD ||
  JSON.parse(readFileSync(new URL("../../work/fullstack/local-login.json",import.meta.url),"utf-8")).password;}
async function login(page:Page,role="support"){
  await page.goto("/");
  await page.getByLabel("邮箱").fill(role+"@demo.test");
  await page.getByLabel("密码").fill(password());
  await page.getByRole("button",{name:"进入工作台"}).click();
  await expect(page.getByRole("button",{name:"售后助手",exact:true})).toBeVisible();
}
async function openFlow(page:Page,order?:string,kind?:string){
  await page.getByRole("button",{name:"售后助手",exact:true}).click();
  if(order)await page.getByLabel("售后订单号").fill(order);
  if(kind)await page.getByLabel("申请类型",{exact:true}).selectOption(kind);
  if(order)await page.getByLabel("申请原因").fill("浏览器验收：等待超过承诺时间");
  const response=page.waitForResponse(r=>r.url().endsWith("/api/v1/runs")&&r.request().method()==="POST");
  await page.getByRole("button",{name:"开始新流程"}).click();
  return (await (await response).json()).id as string;
}

test("missing details, explicit confirmation and real MCP normal reminder",async({page})=>{
  await login(page);
  await openFlow(page);
  await expect(page.getByRole("heading",{name:"当前流程：等待补充"})).toBeVisible();
  await page.getByLabel("售后订单号").fill(orders.normal);
  await page.getByLabel("申请类型",{exact:true}).selectOption("normal");
  await page.getByLabel("申请原因").fill("浏览器验收：普通催单");
  await page.getByRole("button",{name:"提交补充信息"}).click();
  await expect(page.getByRole("heading",{name:"当前流程：等待确认"})).toBeVisible();
  await expect(page.getByText("尚未创建",{exact:true})).toHaveCount(0);
  await page.getByRole("button",{name:"确认上述申请"}).click();
  await expect(page.getByRole("heading",{name:"当前流程：处理完成"})).toBeVisible();
  await expect(page.getByRole("status")).toContainText("已登记普通催单");
  await expect(page.getByRole("status")).toContainText("尚未通知外部仓库");
    await page.getByText("本流程累计用量",{exact:true}).click();
    const cumulative=await page.getByText(/工具预留尝试/).textContent();
    await page.getByRole("button",{name:"只读核实办理状态"}).click();
    await expect(page.getByText("以上申请状态为人工只读核实结果，不改变Agent流程状态。")).toBeVisible();
    await expect(page.getByText(/工具预留尝试/)).toHaveText(cumulative||"");
    await expect(page.getByRole("heading",{name:"当前流程：处理完成"})).toBeVisible();

});

test("resume after reload, independent approval and actual result",async({page,browser})=>{
  test.setTimeout(90_000);
  await login(page);
  const id=await openFlow(page,orders.expedite,"expedite");
  await expect(page.getByRole("heading",{name:"当前流程：等待确认"})).toBeVisible();
  await page.reload();
  await login(page);
  await page.getByRole("button",{name:"售后助手",exact:true}).click();
  await expect(page.getByLabel("恢复已有流程").locator("option[value='"+id+"']")).toHaveCount(1);
  await page.getByLabel("恢复已有流程").selectOption(id);
  await expect(page.getByRole("heading",{name:"当前流程：等待确认"})).toBeVisible();
  await page.getByRole("button",{name:"确认上述申请"}).click();
  await expect(page.getByRole("heading",{name:"当前流程：等待审批"})).toBeVisible();
  await expect(page.getByText("尚未创建",{exact:true})).toBeVisible();
  const context=await browser.newContext({baseURL:process.env.WORKBENCH_WEB_URL || "http://127.0.0.1:5173"});
  try{
    const reviewer=await context.newPage();
    await login(reviewer,"approver");
    await reviewer.getByRole("button",{name:"加急审批",exact:true}).click();
    const card=reviewer.locator(".document-card").filter({has:reviewer.getByRole("heading",{name:"订单 "+orders.expedite,exact:true})});
    await card.getByRole("button",{name:"批准加急"}).click();
    await expect(card).toHaveCount(0);
    await page.getByRole("button",{name:"刷新审批结果"}).click();
    await expect(page.getByRole("heading",{name:"当前流程：处理完成"})).toBeVisible();
    await expect(page.getByRole("status")).toContainText("尚未通知外部仓库");
    await expect(page.getByText("尚未创建",{exact:true})).toHaveCount(0);
    await page.screenshot({path:fileURLToPath(new URL("../../work/fullstack/aftersales-preview.png",import.meta.url)),fullPage:true});
  }finally{await context.close();}
});

test("editing proposal requires another review and can cancel without submission",async({page})=>{
  await login(page);
  await openFlow(page,orders.correction,"expedite");
  await expect(page.getByRole("heading",{name:"当前流程：等待确认"})).toBeVisible();
  await page.getByLabel("申请类型",{exact:true}).selectOption("normal");
  await expect(page.getByRole("button",{name:"确认上述申请"})).toBeDisabled();
  await page.getByRole("button",{name:"更新信息并重新核对"}).click();
  await expect(page.getByRole("button",{name:"确认上述申请"})).toBeEnabled();
  await expect(page.getByText("普通催单",{exact:true}).first()).toBeVisible();
  await page.getByRole("button",{name:"取消本次申请"}).click();
  await expect(page.getByRole("heading",{name:"当前流程：已停止"})).toBeVisible();
  await expect(page.getByRole("status")).toContainText("未提交申请");
});

test("missing promised timestamp blocks expedited action",async({page})=>{
  await login(page);
  await openFlow(page,orders.missing,"expedite");
  await expect(page.getByRole("heading",{name:"当前流程：处理完成"})).toBeVisible();
  await expect(page.getByRole("status")).toContainText("缺少承诺发货时间");
  await expect(page.getByRole("button",{name:"确认上述申请"})).toHaveCount(0);
});
