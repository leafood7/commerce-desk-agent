import {test,expect} from '@playwright/test';

const SENTINEL='PRIVATE-DRAFT-MUST-NOT-LEAK';
test('review gate publishes only final safe answer, normal policy still completes, pure lookup bypasses model',async({page,request})=>{
  test.setTimeout(180000);
  const password=process.env.WORKBENCH_TEST_PASSWORD;if(!password)throw new Error('Private isolated password required');
  const api=process.env.WORKBENCH_TEST_URL||'http://127.0.0.1:18192';
  const auth=await request.post(api+'/api/v1/auth/login',{data:{organization:'demo-a',email:'admin@demo.test',password}});
  expect(auth.ok()).toBeTruthy();const headers={Authorization:'Bearer '+(await auth.json()).access_token};
  const uploaded=await request.post(api+'/api/v1/knowledge/documents',{headers,multipart:{chunk_strategy:'parent_child',file:{name:'policy.md',mimeType:'text/markdown',buffer:Buffer.from('# 退货规则\n\n普通商品七天内可申请退货，定制商品除外。')}}});
  expect(uploaded.ok()).toBeTruthy();const version=(await uploaded.json()).version.id;
  await expect.poll(async()=> (await (await request.get(api+'/api/v1/knowledge/versions/'+version,{headers})).json()).status,{timeout:60000}).toBe('ready');
  expect((await request.post(api+'/api/v1/knowledge/versions/'+version+'/publish',{headers,data:{}})).ok()).toBeTruthy();
  await page.goto('/');await page.getByLabel('邮箱').fill('admin@demo.test');await page.getByLabel('密码').fill(password);await page.getByRole('button',{name:'进入工作台'}).click();
  await page.getByRole('button',{name:'配置版本',exact:true}).click();
  await page.getByLabel('模型选择').selectOption('server_default');
  await page.getByLabel('政策回答方式').selectOption('reviewed');
  await page.getByRole('button',{name:'保存配置草稿'}).click();
  await expect(page.getByRole('status')).toContainText('已保存草稿');
  await page.getByRole('button',{name:'发布选中版本'}).click();
  await expect(page.getByRole('status')).toContainText('已发布');
  await page.getByRole('button',{name:'政策检索',exact:true}).click();
  await page.getByLabel('政策问题').fill('普通商品退货期限 BLOCK-ME');
  const responsePromise=page.waitForResponse(r=>r.url().endsWith('/api/v1/runs')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'检索政策',exact:true}).click();
  const id=(await (await responsePromise).json()).id;
  await expect(page.getByText('正在核对回答依据',{exact:true}).first()).toBeVisible({timeout:60000});
  const during=await request.get(api+'/api/v1/runs/'+id,{headers});expect(await during.text()).not.toContain(SENTINEL);
  await expect(page.getByRole('status')).toContainText('当前回答未通过核验',{timeout:60000});
  expect(await page.locator('body').innerText()).not.toContain(SENTINEL);
  const blocked=(await (await request.get(api+'/api/v1/runs/'+id,{headers})).json());
  expect(blocked.output.answer_gate.status).toBe('blocked');
  const checkedUsage=await (await request.get(api+'/api/v1/runs/'+id+'/usage',{headers})).json();
  expect(checkedUsage.model_attempts).toBe(2);expect(checkedUsage.max_model_attempts).toBe(2);
  for(const path of ['/runs/'+id+'/events','/conversations/'+blocked.conversation_id+'/messages','/conversations/'+blocked.conversation_id+'/runs']){
    expect(await (await request.get(api+'/api/v1'+path,{headers})).text()).not.toContain(SENTINEL);
  }
  await page.screenshot({path:'../work/answer-gate-v1/blocked-answer.png',fullPage:true});
  await page.getByLabel('政策问题').fill('普通商品退货期限是什么？');
  await page.getByRole('button',{name:'检索政策',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('普通商品七天内可申请退货',{timeout:60000});
  const lookup=await request.post(api+'/api/v1/runs',{headers,data:{kind:'order_lookup',order_number:'A1001'}});
  expect(lookup.ok()).toBeTruthy();const lookupId=(await lookup.json()).id;
  await expect.poll(async()=> (await (await request.get(api+'/api/v1/runs/'+lookupId,{headers})).json()).status,{timeout:60000}).toBe('completed');
  const usage=await (await request.get(api+'/api/v1/runs/'+lookupId+'/usage',{headers})).json();
  expect(usage.model_attempts).toBe(0);
});
