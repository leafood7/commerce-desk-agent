import {test,expect,type Page} from "@playwright/test";
import {readFileSync} from "node:fs";
function password(){return process.env.WORKBENCH_TEST_PASSWORD||
  JSON.parse(readFileSync(new URL("../../work/fullstack/local-login.json",import.meta.url),"utf-8")).password;}
async function login(page:Page,role:string){
  await page.goto("/");
  await page.getByLabel("商家编号").fill("demo-b");
  await page.getByLabel("邮箱").fill(role+"@demo.test");
  await page.getByLabel("密码").fill(password());
  const result=page.waitForResponse(r=>r.url().endsWith("/api/v1/auth/login"));
  await page.getByRole("button",{name:"进入工作台"}).click();
  return (await (await result).json()).access_token as string;
}

test("admin drafts and publishes controlled configuration; restore previous settings",async({page})=>{
  const token=await login(page,"admin");
  const headers={Authorization:"Bearer "+token};
  const original=(await page.request.get("/api/v1/runtime/config",{headers})).json();
  const saved=await original;let publishedId:string|null=null;
  try{
    await page.getByRole("button",{name:"配置版本",exact:true}).click();
    await expect(page.getByRole("heading",{name:"配置版本",exact:true})).toBeVisible();
    await page.getByLabel("检索候选数").selectOption("1");
    await page.getByLabel("回复用语").selectOption("friendly");
    await page.getByRole("button",{name:"保存配置草稿"}).click();
    await expect(page.getByRole("status")).toContainText("尚未影响运行");
    const response=page.waitForResponse(r=>r.url().endsWith("/publish")&&r.request().method()==="POST");
    await page.getByRole("button",{name:"发布选中版本"}).click();
    const result=await (await response).json();publishedId=result.current.id;
    await expect(page.getByRole("status")).toContainText("仅影响之后创建的新运行");
    await expect(page.getByText("用语：友好问候")).toBeVisible();
    expect(result.current.values.model_alias).toBe("rules");
  }finally{
    const current=await (await page.request.get("/api/v1/runtime/config",{headers})).json();
    if(publishedId&&current.current.id===publishedId){
      let target=saved.current;
      if(!target.id){
        const values={...target.values};delete values.prompt_sha256;
        const response=await page.request.post("/api/v1/runtime/config/versions",{headers,data:values});
        expect(response.ok()).toBeTruthy();target=await response.json();
      }
      const response=await page.request.post("/api/v1/runtime/config/versions/"+target.id+"/publish",
        {headers,data:{expected_revision:current.revision}});
      expect(response.ok()).toBeTruthy();
    }
  }
});

test("conversation journal groups new tasks and opens an existing flow without recreating it",async({page})=>{
  await login(page,"support");let creations=0;
  page.on("request",request=>{if(request.method()==="POST"&&request.url().endsWith("/api/v1/runs"))creations++;});
  await page.getByRole("button",{name:"售后助手",exact:true}).click();
  await page.getByLabel("售后需求").fill("请查询订单 B2001 的物流状态");
  const firstResponse=page.waitForResponse(r=>r.url().endsWith("/api/v1/runs")&&r.request().method()==="POST");
  await page.getByRole("button",{name:"开始新流程"}).click();
  const first=await (await firstResponse).json();
  await expect(page.getByRole("heading",{name:"当前流程：处理完成"})).toBeVisible();
  await page.getByRole("button",{name:"会话记录",exact:true}).click();
  await page.getByLabel("选择会话").selectOption(first.conversation_id);
  await expect(page.locator(".conversation-journal")).toContainText("B2001");
  await page.getByRole("button",{name:"在此会话开始新任务"}).click();
  await page.getByLabel("售后需求").fill("再查询订单 B2001 当前状态");
  const secondResponse=page.waitForResponse(r=>r.url().endsWith("/api/v1/runs")&&r.request().method()==="POST");
  await page.getByRole("button",{name:"开始新流程"}).click();
  const second=await (await secondResponse).json();
  expect(second.conversation_id).toBe(first.conversation_id);
  expect(second.id).not.toBe(first.id);
  await expect(page.getByRole("heading",{name:"当前流程：处理完成"})).toBeVisible();
  await page.getByRole("button",{name:"会话记录",exact:true}).click();
  await page.getByLabel("选择会话").selectOption(first.conversation_id);
  await expect(page.locator(".conversation-journal article")).toHaveCount(4);
  await page.getByText("该会话的近期流程",{exact:true}).click();
  await page.getByRole("button",{name:"打开售后流程 "+first.id.slice(0,8),exact:true}).click();
  await expect(page.getByRole("heading",{name:"当前流程：处理完成"})).toBeVisible();
  await expect(page.getByLabel("售后需求")).toHaveValue(first.input.query);
  expect(creations).toBe(2);
  await page.getByRole("button",{name:"在当前会话准备新任务",exact:true}).click();
  await expect(page.getByLabel("售后需求")).toHaveValue("");
  await expect(page.getByLabel("售后订单号")).toHaveValue("");
  await page.getByLabel("售后需求").fill("请帮我催发货");
  const pausedResponse=page.waitForResponse(r=>r.url().endsWith("/api/v1/runs")&&r.request().method()==="POST");
  await page.getByRole("button",{name:"开始新流程"}).click();
  const paused=await (await pausedResponse).json();
  expect(paused.conversation_id).toBe(first.conversation_id);
  await expect(page.getByRole("heading",{name:"当前流程：等待补充"})).toBeVisible();
  await page.getByRole("button",{name:"会话记录",exact:true}).click();
  await page.getByLabel("选择会话").selectOption(first.conversation_id);
  await expect(page.locator(".conversation-journal article")).toHaveCount(6);
  await page.getByText("该会话的近期流程",{exact:true}).click();
  await page.getByRole("button",{name:"打开售后流程 "+paused.id.slice(0,8),exact:true}).click();
  await expect(page.getByRole("heading",{name:"当前流程：等待补充"})).toBeVisible();
  expect(creations).toBe(3);
  await page.getByRole("button",{name:"停止跟进",exact:true}).click();
  await expect(page.getByRole("heading",{name:"当前流程：已停止"})).toBeVisible();
});
