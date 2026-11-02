import {test,expect} from '@playwright/test';
test('operator follows API to MCP, retrieval and database counters in trace viewer',async({page,request})=>{
 test.setTimeout(120000);
 const base=process.env.WORKBENCH_TEST_URL,password=process.env.WORKBENCH_TEST_PASSWORD;
 if(!base||!password)throw new Error('Isolated functional environment required');
 const auth=await request.post(base+'/api/v1/auth/login',{data:{organization:'demo-a',email:'support@demo.test',password}});expect(auth.ok()).toBeTruthy();const headers={Authorization:'Bearer '+(await auth.json()).access_token};
 const docs=await(await request.get(base+'/api/v1/knowledge/documents?limit=100',{headers})).json();const target=docs.items.find((d:any)=>d.title==='星砂期限FAQ联动演示'&&d.active_version_id);expect(target).toBeTruthy();
 const response=await request.post(base+'/api/v1/knowledge/search',{headers,data:{query:'星砂盒退货期限',policy_scope:'returns',version_id:target.active_version_id,top_k:3}});expect(response.ok()).toBeTruthy();const trace=response.headers()['x-trace-id'];expect(trace).toBeTruthy();
 const detail=await request.get(base+'/api/v1/traces/'+trace,{headers});expect(detail.ok()).toBeTruthy();const rows=(await detail.json()).items;for(const service of ['api','mcp_client','mcp_server','business','retrieval'])expect(rows.some((r:any)=>r.service===service)).toBe(true);
 await page.goto('/');await page.getByLabel('邮箱').fill('support@demo.test');await page.getByLabel('密码').fill(password);await page.getByRole('button',{name:'进入工作台'}).click();await page.getByRole('button',{name:'执行追踪',exact:true}).click();await page.getByLabel('请求追踪编号').fill(trace);await page.getByRole('button',{name:'查看请求',exact:true}).click();await expect(page.getByRole('heading',{name:'请求 '+trace})).toBeVisible();await expect(page.getByText('知识检索 · vector_search',{exact:true})).toBeVisible();await expect(page.getByText('MCP服务 · search_knowledge_base',{exact:true})).toBeVisible();
 await page.screenshot({path:'../work/trace-v1/trace-viewer.png',fullPage:true});
 const denied=await request.get(base+'/api/v1/traces/'+trace);expect(denied.status()).toBe(401);
});
