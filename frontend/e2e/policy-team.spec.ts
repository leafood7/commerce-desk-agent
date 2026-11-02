import {test,expect} from '@playwright/test';
test('multi-topic policy team hands off sources and keeps a public task record',async({page,request})=>{
 test.setTimeout(180000);
 const password=process.env.WORKBENCH_TEST_PASSWORD,base=process.env.WORKBENCH_TEST_URL;
 if(!password||!base)throw new Error('Isolated local functional environment required');
 async function auth(role:string){const r=await request.post(base+'/api/v1/auth/login',{data:{organization:'demo-a',email:role+'@demo.test',password}});expect(r.ok()).toBeTruthy();return {Authorization:'Bearer '+(await r.json()).access_token};}
 const admin=await auth('admin'),approver=await auth('approver');
 const audience={merchant:'星砂协作演示店',region:'隔离地区',channel:'演示官网',product:'星砂盒'};
 const uploaded=await request.post(base+'/api/v1/knowledge/documents',{headers:admin,multipart:{title:'多事项协作退货演示',policy_scope:'returns',chunk_strategy:'parent_child',chunk_chars:'180',chunk_overlap:'20',audience:JSON.stringify(audience),file:{name:'team-policy.md',mimeType:'text/markdown',buffer:Buffer.from('# 第一条 范围\n本演示规则仅适用于星砂盒普通商品，定制商品除外。\n\n# 第四条 期限\n普通商品自实际签收后8天内可申请退货，须核对第一条的商品范围。\n\n# 第十二条 运费\n第四条的普通商品无理由退货运费由用户承担。')}}});
 expect(uploaded.ok()).toBeTruthy();const version=(await uploaded.json()).version.id;
 await expect.poll(async()=> (await(await request.get(base+'/api/v1/knowledge/versions/'+version,{headers:admin})).json()).status,{timeout:60000}).toBe('ready');
 let r=await request.post(base+'/api/v1/knowledge/versions/'+version+'/policy-extractions',{headers:admin,data:{}});expect(r.ok()).toBeTruthy();let row=await r.json();
 r=await request.post(base+'/api/v1/knowledge/policy-extractions/'+row.id+'/submit',{headers:admin,data:{expected_revision:row.revision,cards:row.payload.cards,note:'核对原文范围期限例外与运费条款'}});expect(r.ok()).toBeTruthy();row=await r.json();
 r=await request.post(base+'/api/v1/knowledge/policy-extractions/'+row.id+'/review',{headers:approver,data:{expected_revision:row.revision,action:'approve',acknowledge_warnings:true,note:'独立核对全文并确认条款位置'}});expect(r.ok()).toBeTruthy();row=await r.json();
 expect((await request.post(base+'/api/v1/knowledge/policy-extractions/'+row.id+'/publish',{headers:admin,data:{expected_revision:row.revision,acknowledge_warnings:true,note:'按审核结果发布演示政策'}})).ok()).toBeTruthy();
 await page.goto('/');await page.getByLabel('邮箱').fill('admin@demo.test');await page.getByLabel('密码').fill(password);await page.getByRole('button',{name:'进入工作台'}).click();await page.getByRole('button',{name:'配置版本',exact:true}).click();
 await page.getByLabel('模型选择').selectOption('server_default');await page.getByLabel('政策回答方式').selectOption('reviewed');await page.getByLabel('退货动作决策').selectOption('model');await page.getByLabel('退货流程').selectOption('guided_v3');await page.getByLabel('退货政策范围').selectOption('clause_bound');await page.getByLabel('按需阅读原文').selectOption('true');await page.getByLabel('政策协作模式').selectOption('collaborative');await page.getByLabel('协作执行方式').selectOption('task_board_v2');await page.getByLabel('检索候选数').selectOption('1');
 await page.getByRole('button',{name:'保存配置草稿'}).click();await expect(page.getByRole('status')).toContainText('已保存草稿');await page.getByRole('button',{name:'发布选中版本'}).click();await expect(page.getByRole('status')).toContainText('已发布');
 await page.getByRole('button',{name:'政策检索',exact:true}).click();await page.getByLabel('问题涉及的范围').selectOption('returns');await page.getByLabel('商家',{exact:true}).fill(audience.merchant);await page.getByLabel('地区',{exact:true}).fill(audience.region);await page.getByLabel('购买渠道',{exact:true}).fill(audience.channel);await page.getByLabel('商品范围',{exact:true}).fill(audience.product);
 await page.getByLabel('政策问题').fill('星砂盒普通商品退货期限、运费责任和定制例外，只解释规则，不申请。');
 const response=page.waitForResponse(r=>r.url().endsWith('/api/v1/runs')&&r.request().method()==='POST');await page.getByRole('button',{name:'检索政策',exact:true}).click();const runId=(await(await response).json()).id;
 await expect(page.getByRole('button',{name:'检索政策',exact:true})).toBeEnabled({timeout:60000});const taskBoard=page.getByRole('article',{name:'政策协作事项'});await expect(taskBoard).toBeVisible();for(const text of ['退货期限','费用与责任','例外条款'])await expect(taskBoard.getByText(text,{exact:true})).toBeVisible();
 const result=await(await request.get(base+'/api/v1/runs/'+runId,{headers:admin})).json();expect(result.status).toBe('completed');expect(result.output.answer_gate.status).toBe('approved');expect(result.output.answer_gate.collaboration_version).toBe('task_board_v2');expect(result.output.answer_gate.collaboration.tasks.length).toBe(3);
 const events=await(await request.get(base+'/api/v1/runs/'+runId+'/events',{headers:admin})).text();expect(events).toContain('evidence_coordinator');expect(events).not.toContain('create_return_draft');expect(events).not.toContain('task_answers');
 await page.screenshot({path:'../work/policy-team-v2/policy-task-board.png',fullPage:true});
 await page.getByRole('button',{name:'会话记录',exact:true}).click();await page.getByLabel('选择会话').selectOption(result.conversation_id);await page.getByText('该会话的近期流程',{exact:true}).click();await page.getByRole('button',{name:'查看协作记录 '+runId.slice(0,8),exact:true}).click();await expect(page.getByRole('article',{name:'政策协作事项'})).toBeVisible();
});
