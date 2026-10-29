import {test,expect,type Page} from "@playwright/test";
import {readFileSync} from "node:fs";

const fixture=JSON.parse(readFileSync(new URL("../../work/returns-v1/demo.json",import.meta.url),"utf-8"));
const password=()=>process.env.WORKBENCH_TEST_PASSWORD||JSON.parse(readFileSync(new URL("../../work/fullstack/local-login.json",import.meta.url),"utf-8")).password;
async function login(page:Page,role="support"){
  await page.goto("/");await page.getByLabel("邮箱").fill(role+"@demo.test");await page.getByLabel("密码").fill(password());
  await page.getByRole("button",{name:"进入工作台"}).click();await page.getByRole("button",{name:"退货申请",exact:true}).click();
}

test("inspection operator records result; waiting Agent refreshes into independent approval",async({page,browser,request})=>{
  test.setTimeout(150_000);
  const base=(process.env.WORKBENCH_TEST_URL||"http://127.0.0.1:18082")+"/api/v1";
  const auth=await request.post(base+"/auth/login",{data:{organization:"demo-a",email:"admin@demo.test",password:password()}});
  expect(auth.ok()).toBeTruthy();const headers={Authorization:"Bearer "+(await auth.json()).access_token};
  const original=await (await request.get(base+"/runtime/config",{headers})).json();
  const version=await request.post(base+"/runtime/config/versions",{headers,data:{return_flow_version:"guided_v3",return_retrieval_mode:"policy_scoped",model_alias:"rules",return_decisions:"rules"}});
  expect(version.ok()).toBeTruthy();const owned=(await version.json()).id;
  expect((await request.post(`${base}/runtime/config/versions/${owned}/publish`,{headers,data:{expected_revision:original.revision}})).ok()).toBeTruthy();
  const operatorContext=await browser.newContext({baseURL:process.env.WORKBENCH_WEB_URL||"http://127.0.0.1:15173"});
  try{
    await login(page);await page.getByLabel("退货订单号").fill(fixture.orders.normal.number);
    await page.getByRole("button",{name:"读取退货订单"}).click();
    await page.getByLabel("退货规则",{exact:true}).selectOption(fixture.quality_policy_id);
    await page.getByLabel("商品状态",{exact:true}).selectOption("unused");
    await page.getByLabel("退货原因").fill("浏览器质检业务闭环");
    await page.getByLabel("退货核对需求").fill("申请退货，核实质检结果及适用政策。");
    await page.getByRole("button",{name:"启动退货 Agent 核对"}).click();
    await expect(page.getByRole("button",{name:"刷新质检结果并重新核对"})).toBeVisible({timeout:40_000});
    await expect(page.getByRole("button",{name:"确认已核对的退货预览"})).toHaveCount(0);
    await page.getByRole("button",{name:"为当前商品及数量创建演示质检"}).click();
    await expect(page.getByLabel("选择质检工单")).not.toHaveValue("");
    const inspection=await page.getByLabel("选择质检工单").inputValue();expect(inspection).toBeTruthy();
    const operator=await operatorContext.newPage();await login(operator,"approver");
    await operator.getByLabel("选择质检工单").selectOption(inspection);
    await operator.getByLabel("质检处理说明").fill("开始独立检查演示商品");
    await operator.getByRole("button",{name:"开始演示质检"}).click();
    await expect(operator.getByRole("status")).toContainText("处理中");
    await expect(operator.getByLabel("质检处理说明")).toBeEditable();
    await operator.getByLabel("质检处理说明").fill("演示检查通过，仅供本地验收");
    await operator.getByLabel("演示质检结论").selectOption("passed");
    await operator.getByRole("button",{name:"登记演示质检结果"}).click();
    await expect(operator.getByRole("status")).toContainText("结论：通过");
    await page.getByRole("button",{name:"刷新质检结果并重新核对"}).click();
    await expect(page.getByRole("button",{name:"确认已核对的退货预览"})).toBeEnabled({timeout:40_000});
    await expect(page.getByText("实际质检结论 · 实际字段已提供",{exact:false})).toContainText("演示质检通过");
    await page.getByRole("button",{name:"确认已核对的退货预览"}).click();
    await expect(page.getByRole("status").filter({hasText:"已提交独立审批"})).toBeVisible({timeout:40_000});
    await page.getByRole("button",{name:"停止本次退货跟进"}).click();
  }finally{
    await operatorContext.close();
    const current=await (await request.get(base+"/runtime/config",{headers})).json();
    if(current.current.id===owned){
      let previous=original.current.id;
      if(!previous){const values={...original.current.values};delete values.prompt_sha256;delete values.return_prompt_sha256;const saved=await request.post(base+"/runtime/config/versions",{headers,data:values});expect(saved.ok()).toBeTruthy();previous=(await saved.json()).id;}
      expect((await request.post(`${base}/runtime/config/versions/${previous}/publish`,{headers,data:{expected_revision:current.revision}})).ok()).toBeTruthy();
    }
  }
});

test("used item after ten days needs explicit defect branch and rechecks correction",async({page,browser,request})=>{
  test.setTimeout(150_000);
  const base=(process.env.WORKBENCH_TEST_URL||"http://127.0.0.1:18082")+"/api/v1";
  const auth=await request.post(base+"/auth/login",{data:{organization:"demo-a",email:"admin@demo.test",password:password()}});
  expect(auth.ok()).toBeTruthy();const headers={Authorization:"Bearer "+(await auth.json()).access_token};
  const original=await (await request.get(base+"/runtime/config",{headers})).json();
  const version=await request.post(base+"/runtime/config/versions",{headers,data:{return_flow_version:"guided_v3",return_retrieval_mode:"clause_bound",model_alias:"rules",return_decisions:"rules"}});
  expect(version.ok()).toBeTruthy();const owned=(await version.json()).id;
  expect((await request.post(`${base}/runtime/config/versions/${owned}/publish`,{headers,data:{expected_revision:original.revision}})).ok()).toBeTruthy();
  const operatorContext=await browser.newContext({baseURL:process.env.WORKBENCH_WEB_URL||"http://127.0.0.1:15173"});
  try{
    await login(page);
    await page.getByRole("button",{name:"配置版本",exact:true}).click();
    await expect(page.getByText("当前分支已召回原文组装",{exact:false})).toBeVisible();
    await page.getByRole("button",{name:"退货申请",exact:true}).click();
    await page.getByLabel("退货订单号").fill(fixture.orders.quality_window.number);
    await page.getByRole("button",{name:"读取退货订单"}).click();
    await page.getByLabel("退货规则",{exact:true}).selectOption(fixture.defect_policy_id);
    await page.getByLabel("退货分支",{exact:true}).selectOption("quality_issue");
    await page.getByLabel("商品状态",{exact:true}).selectOption("used");
    await page.getByLabel("退货原因").fill("已使用十天内发现质量问题的隔离演示");
    await page.getByRole("button",{name:"启动退货 Agent 核对"}).click();
    await expect(page.getByRole("button",{name:"刷新质检结果并重新核对"})).toBeVisible({timeout:40_000});
    await page.getByRole("button",{name:"为当前商品及数量创建演示质检"}).click();
    await expect(page.getByLabel("选择质检工单")).not.toHaveValue("");
    const inspection=await page.getByLabel("选择质检工单").inputValue();
    const operator=await operatorContext.newPage();await login(operator,"approver");
    await operator.getByLabel("选择质检工单").selectOption(inspection);
    await operator.getByLabel("质检处理说明").fill("开始演示缺陷检查");
    await operator.getByRole("button",{name:"开始演示质检"}).click();
    await expect(operator.getByRole("status")).toContainText("处理中");
    await expect(operator.getByLabel("质检处理说明")).toBeEditable();
    await operator.getByLabel("质检处理说明").fill("明确确认缺陷，仅隔离演示");
    await operator.getByLabel("独立缺陷判定").selectOption("defect_confirmed");
    await operator.getByRole("button",{name:"登记演示质检结果"}).click();
    await expect(operator.getByRole("status")).toContainText("结论：未通过");
    await page.getByRole("button",{name:"刷新质检结果并重新核对"}).click();
    await expect(page.getByRole("button",{name:"确认已核对的退货预览"})).toBeEnabled({timeout:40_000});
    const context=await page.getByText(/^当前分支绑定原文：/).allTextContents();
    expect(context.join("\n")).toContain("质量问题退货分支限实际签收30天内");
    expect(context.join("\n")).not.toContain("仅演示未使用商品的无理由退货申请");
    await page.getByLabel("退货分支",{exact:true}).selectOption("no_reason");
    await page.getByLabel("退货规则",{exact:true}).selectOption(fixture.policy_id);
    await expect(page.getByRole("button",{name:"确认已核对的退货预览"})).toBeDisabled();
    await page.getByRole("button",{name:"按当前信息重新核对"}).click();
    await expect(page.getByRole("status").filter({hasText:"不满足当前规则的未使用条件"})).toBeVisible({timeout:40_000});
    await expect(page.getByRole("button",{name:"确认已核对的退货预览"})).toHaveCount(0);
  }finally{
    await operatorContext.close();
    const current=await (await request.get(base+"/runtime/config",{headers})).json();
    if(current.current.id===owned){
      let previous=original.current.id;
      if(!previous){const values={...original.current.values};delete values.prompt_sha256;delete values.return_prompt_sha256;const saved=await request.post(base+"/runtime/config/versions",{headers,data:values});expect(saved.ok()).toBeTruthy();previous=(await saved.json()).id;}
      expect((await request.post(`${base}/runtime/config/versions/${previous}/publish`,{headers,data:{expected_revision:current.revision}})).ok()).toBeTruthy();
    }
  }
});
