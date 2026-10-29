import {test,expect} from "@playwright/test";
import {readFileSync} from "node:fs";

test("return policy picker scopes loaded rules to selected SKU and never auto-selects another order rule",async({page})=>{
  const fixture=JSON.parse(readFileSync(new URL("../../work/returns-v1/demo.json",import.meta.url),"utf-8"));
  const password=process.env.WORKBENCH_TEST_PASSWORD||JSON.parse(readFileSync(new URL("../../work/fullstack/local-login.json",import.meta.url),"utf-8")).password;
  await page.goto("/");await page.getByLabel("邮箱").fill("support@demo.test");await page.getByLabel("密码").fill(password);
  await page.getByRole("button",{name:"进入工作台"}).click();await page.getByRole("button",{name:"退货申请",exact:true}).click();
  await page.getByLabel("退货订单号").fill(fixture.orders.normal.number);
  await page.getByRole("button",{name:"读取退货订单"}).click();
  const selector=page.getByLabel("退货规则",{exact:true});
  await expect(selector.locator("option")).toHaveCount(4);
  await expect(selector).toHaveValue("");
  const ids=await selector.locator("option").evaluateAll(options=>options.map(o=>(o as HTMLOptionElement).value).filter(Boolean));
  expect(ids.sort()).toEqual([fixture.policy_id,fixture.quality_policy_id,fixture.defect_policy_id].sort());
  await expect(page.getByText("当前商品："+fixture.sku,{exact:false})).toBeVisible();
  await expect(selector.locator("option").filter({hasText:"需独立确认缺陷"})).toHaveCount(1);
  await expect(selector.locator("option").filter({hasText:"需质检通过"})).toHaveCount(1);
  await expect(selector.locator("option").filter({hasText:"无需质检分支"})).toHaveCount(1);
  await selector.selectOption(fixture.policy_id);
  await page.getByRole("button",{name:"刷新退货记录",exact:true}).click();
  await expect(selector).toHaveValue(fixture.policy_id);
  await page.getByLabel("退货订单号").fill(fixture.orders.expired.number);
  await expect(page.getByRole("button",{name:"启动退货 Agent 核对"})).toHaveCount(0);
  await page.getByRole("button",{name:"读取退货订单"}).click();
  await expect(selector).toHaveValue("");
  await expect(selector.locator("option")).toHaveCount(4);
});
