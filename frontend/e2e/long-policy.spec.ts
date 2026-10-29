import {test,expect} from "@playwright/test";
import {readFileSync} from "node:fs";

test("long policy upload and source pagination expose remote exclusion with original text",async({page,request})=>{
  test.setTimeout(180_000);
  const password=process.env.WORKBENCH_TEST_PASSWORD||JSON.parse(readFileSync(new URL("../../work/fullstack/local-login.json",import.meta.url),"utf-8")).password;
  const corpus=JSON.parse(readFileSync(new URL("../../data/evaluation/rag_long_scale_v2/corpus.json",import.meta.url),"utf-8"));
  await page.goto("/");await page.getByLabel("邮箱").fill("admin@demo.test");await page.getByLabel("密码").fill(password);
  const authenticated=page.waitForResponse(r=>r.url().endsWith("/auth/login")&&r.request().method()==="POST");
  await page.getByRole("button",{name:"进入工作台"}).click();
  const headers={Authorization:"Bearer "+(await (await authenticated).json()).access_token};
  await page.getByRole("button",{name:"知识库",exact:true}).click();
  const title="长政策分页验收-"+Date.now();
  await page.getByLabel("文档名称").fill(title);await page.getByLabel("适用范围",{exact:true}).selectOption("returns");
  await page.getByLabel("政策文件").setInputFiles({name:title+".md",mimeType:"text/markdown",buffer:Buffer.from("# "+title+"\n\n"+corpus[0].text)});
  const uploadedResponse=page.waitForResponse(r=>r.url().endsWith("/knowledge/documents")&&r.request().method()==="POST");
  await page.getByRole("button",{name:"上传并处理"}).click();const uploaded=await (await uploadedResponse).json();
  const card=page.locator(".document-card").filter({has:page.getByRole("heading",{name:title,exact:true})});
  try{
    await expect(card.getByRole("button",{name:"发布 v1"})).toBeVisible({timeout:120_000});
    await card.getByRole("button",{name:"发布 v1"}).click();await expect(card).toContainText("当前已发布");
    await page.getByRole("button",{name:"退货申请",exact:true}).click();
    await page.getByText("登记人工核对的退货规则",{exact:true}).click();
    await page.getByRole("button",{name:"加载已发布退货政策"}).click();
    await page.getByLabel("退货政策版本").selectOption(uploaded.version.id);
    const condition=page.getByRole("combobox",{name:/^商品状态条款/});
    await expect.poll(()=>condition.locator("option").count()).toBeGreaterThan(1);
    const remote=condition.locator("option").filter({hasText:"排除人为损坏"});
    await expect(remote).toHaveCount(0);
    for(let pageNumber=0;pageNumber<5&&await remote.count()===0;pageNumber++){
      const before=await condition.locator("option").count();
      await page.getByRole("button",{name:"加载后续政策片段"}).click();
      await expect.poll(()=>condition.locator("option").count()).toBeGreaterThan(before);
    }
    await expect(remote).toHaveCount(1);
    const value=await remote.getAttribute("value");expect(value).toBeTruthy();await condition.selectOption(value!);
    await expect(page.getByLabel("商品状态条款原文",{exact:true})).toHaveValue(/排除人为损坏/);
  }finally{
    await request.post("/api/v1/knowledge/documents/"+uploaded.document_id+"/withdraw",{headers,data:{}});
  }
});
