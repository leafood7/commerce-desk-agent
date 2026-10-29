import {test,expect} from "@playwright/test";
import {readFileSync} from "node:fs";

test("rule question and explicit read-only return keep photo evidence without applying",async({page,request})=>{
  test.setTimeout(120_000);
  const password=process.env.WORKBENCH_TEST_PASSWORD||JSON.parse(readFileSync(new URL("../../work/fullstack/local-login.json",import.meta.url),"utf-8")).password;
  const fixture=JSON.parse(readFileSync(new URL("../../work/returns-v1/demo.json",import.meta.url),"utf-8"));
  const base=(process.env.WORKBENCH_TEST_URL||"http://127.0.0.1:18082")+"/api/v1";
  const auth=await request.post(base+"/auth/login",{data:{organization:"demo-a",email:"admin@demo.test",password}});
  expect(auth.ok()).toBeTruthy();const admin={Authorization:"Bearer "+(await auth.json()).access_token};
  const original=await (await request.get(base+"/runtime/config",{headers:admin})).json();
  const version=await request.post(base+"/runtime/config/versions",{headers:admin,data:{return_flow_version:"guided_v3",return_retrieval_mode:"clause_bound",return_decisions:"rules",model_alias:"rules"}});
  expect(version.ok()).toBeTruthy();const owned=(await version.json()).id;
  expect((await request.post(`${base}/runtime/config/versions/${owned}/publish`,{headers:admin,data:{expected_revision:original.revision}})).ok()).toBeTruthy();
  try{
    await page.goto("/");await page.getByLabel("邮箱").fill("support@demo.test");await page.getByLabel("密码").fill(password);
    const logged=page.waitForResponse(r=>r.url().endsWith("/auth/login")&&r.request().method()==="POST");
    await page.getByRole("button",{name:"进入工作台"}).click();const headers={Authorization:"Bearer "+(await (await logged).json()).access_token};
    const before=await (await request.get(base+"/returns/requests",{headers})).json();
    await page.getByRole("button",{name:"政策检索",exact:true}).click();
    await expect(page.getByText("此入口用于规则问答",{exact:false})).toBeVisible();
    await page.getByLabel("政策问题").fill("照片能自动证明缺陷吗？是否仍需独立缺陷结论？");
    await page.getByLabel("问题涉及的范围").selectOption("returns");
    await page.getByRole("button",{name:"检索政策",exact:true}).click();
    await expect(page.getByRole("status")).toContainText("已找到已发布的政策原文",{timeout:40_000});
    await expect(page.locator(".evidence").filter({hasText:"照片"}).first()).toBeVisible();
    await page.getByRole("button",{name:"退货申请",exact:true}).click();
    await page.getByLabel("退货订单号").fill(fixture.orders.normal.number);
    await page.getByRole("button",{name:"读取退货订单"}).click();
    await page.getByLabel("退货规则",{exact:true}).selectOption(fixture.policy_id);
    await page.getByLabel("退货原因").fill("仅核对照片规则，不提交申请");
    await page.getByLabel("退货核对需求").fill("只查质量问题分支允许已使用商品，但照片能代替独立处理人登记的存在缺陷结论吗？不要申请。");
    await page.getByRole("button",{name:"启动退货 Agent 核对"}).click();
    await expect(page.getByRole("status")).toContainText("未进入申请确认",{timeout:40_000});
    await expect(page.getByText(/^只读查询检索原文：/).filter({hasText:"照片"}).first()).toBeVisible();
    await expect(page.getByRole("button",{name:"确认已核对的退货预览"})).toHaveCount(0);
    const after=await (await request.get(base+"/returns/requests",{headers})).json();
    expect(after.items.map((r:any)=>r.id).sort()).toEqual(before.items.map((r:any)=>r.id).sort());
  }finally{
    const current=await (await request.get(base+"/runtime/config",{headers:admin})).json();
    if(current.current.id===owned){
      let previous=original.current.id;
      if(!previous){const values={...original.current.values};delete values.prompt_sha256;delete values.return_prompt_sha256;const saved=await request.post(base+"/runtime/config/versions",{headers:admin,data:values});expect(saved.ok()).toBeTruthy();previous=(await saved.json()).id;}
      expect((await request.post(`${base}/runtime/config/versions/${previous}/publish`,{headers:admin,data:{expected_revision:current.revision}})).ok()).toBeTruthy();
    }
  }
});
