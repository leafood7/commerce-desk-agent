import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

test("real upload, worker, MCP retrieval, source isolation and withdrawal", async ({ page, request }) => {
  test.setTimeout(180_000);
  const password = process.env.WORKBENCH_TEST_PASSWORD ||
    JSON.parse(readFileSync(new URL("../../work/fullstack/local-login.json", import.meta.url), "utf-8")).password;
  await page.goto("/");
  await page.getByLabel("邮箱").fill("admin@demo.test");
  await page.getByLabel("密码").fill(password);
  const loginResponse = page.waitForResponse(r => r.url().endsWith("/auth/login") && r.request().method() === "POST");
  await page.getByRole("button", {name:"进入工作台"}).click();
  const token = (await (await loginResponse).json()).access_token;
  const headers = {Authorization:"Bearer " + token};
  await page.getByRole("button", {name:"知识库", exact:true}).click();
  const title = "浏览器验收运费券-" + Date.now();
  const content = "# " + title + "\n\n运费券只能抵扣订单运费，不能抵扣商品金额。\n\n订单退款后，运费券不会当然返还，具体以该券规则为准。";
  await page.getByLabel("文档名称").fill(title);
  await page.getByLabel("适用范围", {exact:true}).selectOption("freight_coupon");
  await page.getByLabel("政策文件").setInputFiles({name:title+".md",mimeType:"text/markdown",buffer:Buffer.from(content)});
  const uploadResponse = page.waitForResponse(r => r.url().endsWith("/knowledge/documents") && r.request().method() === "POST");
  await page.getByRole("button", {name:"上传并处理"}).click();
  const uploaded = await (await uploadResponse).json();
  const version = uploaded.version.id;
  const document = uploaded.document_id;
  const card = page.locator(".document-card").filter({has:page.getByRole("heading",{name:title,exact:true})});
  const search = async () => {
    const response = await request.post("/api/v1/knowledge/search", {
      headers, data:{query:"运费券可以抵扣商品金额吗？",policy_scope:"freight_coupon",strategy:"hybrid_rerank",top_k:3}
    });
    expect(response.ok()).toBeTruthy();
    return (await response.json()).matches as {version_id:string}[];
  };
  try {
    await expect(card.getByRole("button",{name:"发布 v1"})).toBeVisible({timeout:120_000});
    expect((await search()).some(row=>row.version_id===version)).toBeFalsy();
    await card.getByRole("button",{name:"发布 v1"}).click();
    await expect(card).toContainText("当前已发布");
    await page.getByRole("button",{name:"政策检索",exact:true}).click();
    await page.getByLabel("政策问题").fill("运费券可以抵扣商品金额吗？");
    await page.getByLabel("问题涉及的范围").selectOption("freight_coupon");
    await page.getByRole("button",{name:"检索政策",exact:true}).click();
    const source = page.locator(".evidence").filter({has:page.getByRole("heading",{name:title,exact:true})});
    await expect(source).toContainText("不能抵扣商品金额",{timeout:60_000});
    await expect(page.getByText("检索已发布政策",{exact:true})).toBeVisible();
    await page.screenshot({path:fileURLToPath(new URL("../../work/fullstack/knowledge-preview.png", import.meta.url)),fullPage:true});
    const download = page.waitForEvent("download");
    await source.getByRole("button",{name:"查看原文件"}).click();
    expect((await download).suggestedFilename()).toBe(title+".md");
    const otherLogin = await request.post("/api/v1/auth/login",{data:{
      organization:"demo-b",email:"support@demo.test",password}});
    const otherToken=(await otherLogin.json()).access_token;
    const cross = await request.get("/api/v1/knowledge/versions/"+version+"/file", {
      headers:{Authorization:"Bearer "+otherToken}});
    expect(cross.status()).toBe(404);
    await page.getByRole("button",{name:"知识库",exact:true}).click();
    await card.getByRole("button",{name:"撤回当前版本"}).click();
    await expect(card).not.toContainText("当前已发布");
    expect((await search()).some(row=>row.version_id===version)).toBeFalsy();
  } finally {
    if(document) await request.post("/api/v1/knowledge/documents/"+document+"/withdraw",{headers,data:{}});
  }
});
