import {test,expect} from '@playwright/test';
test('admin sees selected store and operator inspects the real Qdrant call',async({page})=>{
 const password=process.env.WORKBENCH_TEST_PASSWORD,trace=process.env.WORKBENCH_VECTOR_TRACE;
 if(!password||!trace)throw new Error('Isolated vector walkthrough required');
 async function login(role:string){await page.goto('/');await page.getByLabel('邮箱').fill(role+'@demo.test');await page.getByLabel('密码').fill(password!);await page.getByRole('button',{name:'进入工作台'}).click();}
 await login('admin');await page.getByRole('button',{name:'知识库',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'向量存储配置：Chroma'})).toBeVisible();
 await page.getByRole('button',{name:'退出登录'}).click();await login('support');await page.getByRole('button',{name:'执行追踪',exact:true}).click();await page.getByLabel('请求追踪编号').fill(trace);await page.getByRole('button',{name:'查看请求',exact:true}).click();await expect(page.getByRole('heading',{name:'请求 '+trace})).toBeVisible();await expect(page.getByText('向量存储 · qdrant.query',{exact:true})).toBeVisible();await page.screenshot({path:'../work/vector-store-v1/adapter-trace.png',fullPage:true});
});
