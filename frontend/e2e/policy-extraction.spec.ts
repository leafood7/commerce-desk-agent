import {confirmKnowledgeImpact} from './knowledge-ui';
import {test,expect} from '@playwright/test';
test('policy extraction edit independent review publication and support consumption',async({page,request})=>{
 test.setTimeout(180000);
 const password=process.env.WORKBENCH_TEST_PASSWORD,base=process.env.WORKBENCH_TEST_URL;
 if(!password||!base)throw new Error('Isolated environment required');
 async function auth(role:string){const r=await request.post(base+'/api/v1/auth/login',{data:{organization:'demo-a',email:role+'@demo.test',password}});expect(r.ok()).toBeTruthy();return {Authorization:'Bearer '+(await r.json()).access_token};}
 const admin=await auth('admin');
 const content='# 范围\n仅适用于本演示商家的星砂盒普通商品。\n\n# 申请期限\n商品实际签收后8天内可申请，商品必须未使用。\n\n# 例外与费用\n刻字定制商品除外。商家承担质量问题退货运费。';
 const r=await request.post(base+'/api/v1/knowledge/documents',{headers:admin,multipart:{title:'政策提取浏览器完整演示',policy_scope:'returns',chunk_strategy:'parent_child',file:{name:'policy-extraction-demo.md',mimeType:'text/markdown',buffer:Buffer.from(content)}}});
 expect(r.ok()).toBeTruthy();const version=(await r.json()).version.id;
 await expect.poll(async()=> (await(await request.get(base+'/api/v1/knowledge/versions/'+version,{headers:admin})).json()).status,{timeout:60000}).toBe('ready');
 async function login(role:string){await page.goto('/');if(await page.getByRole('button',{name:'退出登录'}).isVisible())await page.getByRole('button',{name:'退出登录'}).click();await page.getByLabel('邮箱').fill(role+'@demo.test');await page.getByLabel('密码').fill(password);await page.getByRole('button',{name:'进入工作台'}).click();await page.getByRole('button',{name:'知识库',exact:true}).click();}
 async function panel(){const document=page.locator('article.document-card').filter({has:page.getByRole('heading',{name:'政策提取浏览器完整演示',exact:true})});await document.getByRole('button',{name:'政策提取与审核 v1',exact:true}).click();return page.getByRole('article',{name:'政策候选工作台'});}
 await login('admin');let p=await panel();await p.getByRole('button',{name:'自动提取政策候选',exact:true}).click();
 await expect(p.getByText('候选状态：草稿',{exact:false})).toBeVisible();
 await p.getByLabel('整理后的说明').first().fill('本规则仅适用于本演示商家的星砂盒普通商品。');
 await p.getByLabel('修改、审核或发布说明').fill('已对照原文核对范围期限及定制商品例外');
 await p.getByRole('button',{name:'提交政策独立审核'}).click();await expect(p.getByText('候选状态：待独立审核',{exact:false})).toBeVisible();
 expect((await request.post(base+'/api/v1/knowledge/versions/'+version+'/publish',{headers:admin,data:{}})).status()).toBe(409);
 await login('approver');p=await panel();await p.getByLabel('修改、审核或发布说明').fill('独立核对全部原文片段及费用条件，允许发布');
 await p.getByLabel('已对照原文核对适用范围、条件、例外、未知项及解析警告').check();await p.getByRole('button',{name:'批准政策候选'}).click();await expect(p.getByText('候选状态：已审核待发布',{exact:false})).toBeVisible();
 await login('admin');p=await panel();await p.getByLabel('修改、审核或发布说明').fill('按独立审核意见发布本版本与整理条款');await p.getByLabel('已对照原文核对适用范围、条件、例外、未知项及解析警告').check();
 await p.getByRole('button',{name:'发布已审核政策',exact:true}).click();await confirmKnowledgeImpact(page,'publish');await expect(p.getByText('候选状态：已发布',{exact:false})).toBeVisible();
 await page.screenshot({path:'../work/policy-extraction-v1/published-policy.png',fullPage:true});
 const support=await auth('support');const summary=await request.get(base+'/api/v1/knowledge/versions/'+version+'/policy-summary',{headers:support});expect(summary.ok()).toBeTruthy();expect((await summary.json()).business_rule_activation).toBe(false);
 const search=await request.post(base+'/api/v1/knowledge/search',{headers:support,data:{query:'星砂盒刻字定制商品退货例外',policy_scope:'returns',version_id:version,top_k:3}});expect(search.ok()).toBeTruthy();expect((await search.json()).matches.length).toBeGreaterThan(0);
 const head=await(await request.get(base+'/api/v1/runtime/config',{headers:admin})).json();const config=await request.post(base+'/api/v1/runtime/config/versions',{headers:admin,data:{model_alias:'rules',policy_answer_mode:'excerpts'}});expect(config.ok()).toBeTruthy();expect((await request.post(base+'/api/v1/runtime/config/versions/'+(await config.json()).id+'/publish',{headers:admin,data:{expected_revision:head.revision}})).ok()).toBeTruthy();
 await login('support');await page.getByRole('button',{name:'政策检索',exact:true}).click();await page.getByLabel('问题涉及的范围').selectOption('returns');await page.getByLabel('政策问题').fill('星砂盒刻字定制商品退货例外');await page.getByRole('button',{name:'检索政策',exact:true}).click();await expect(page.getByRole('button',{name:'检索政策',exact:true})).toBeEnabled({timeout:60000});
 const evidence=page.locator('article.evidence').filter({hasText:'政策提取浏览器完整演示'}).first();await evidence.getByRole('button',{name:'查看已审核条款'}).click();await expect(evidence.getByText('人工审核的政策整理（不等于退货资格已确认）',{exact:true})).toBeVisible();
 await page.screenshot({path:'../work/policy-extraction-v1/support-summary.png',fullPage:true});
});
