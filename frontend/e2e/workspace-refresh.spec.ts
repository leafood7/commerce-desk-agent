import {test,expect} from '@playwright/test';
test('new workspace keeps real orders, sidebar modules and mobile navigation',async({page})=>{
 test.setTimeout(120000);await page.setViewportSize({width:1448,height:1086});
 await page.goto('/');await page.getByLabel('邮箱').fill('support@demo.test');await page.getByLabel('密码').fill(process.env.WORKBENCH_TEST_PASSWORD!);await page.getByRole('button',{name:'进入工作台'}).click();await expect(page.getByRole('heading',{name:'订单助手',exact:true})).toBeVisible();
 await expect(page.getByText('从一笔订单开始',{exact:true})).toBeVisible();await page.getByRole('button',{name:'查询订单',exact:true}).click();await expect(page.getByRole('heading',{name:'订单 A1001',exact:true})).toBeVisible({timeout:60000});await expect(page.getByText('已取得订单信息',{exact:true})).toBeVisible();await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow','3');await expect(page.getByText('本次为订单查询，未自动检索政策或判定退货资格。')).toBeVisible();
 await page.screenshot({path:'../work/frontend-refresh-v1/desktop.png',fullPage:true});
 await page.getByRole('button',{name:'查看订单详情',exact:true}).click();await expect(page.getByText('付款状态',{exact:true})).toBeVisible();
 const nav=page.getByRole('navigation',{name:'工作台功能'});for(const label of ['售后助手','退货申请','政策检索','知识库','运行策略','执行追踪','会话记录','配置版本']){await nav.getByRole('button',{name:label,exact:true}).click();await expect(nav.getByRole('button',{name:label,exact:true})).toHaveAttribute('aria-current','page');await expect(page.locator('.desk-module h2').first()).toBeVisible();}
 await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'切换工作台导航'}).click();await nav.getByRole('button',{name:'订单查询',exact:true}).click();await expect(page.getByRole('heading',{name:'订单助手',exact:true})).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);await page.screenshot({path:'../work/frontend-refresh-v1/mobile.png',fullPage:true});
 await page.getByRole('button',{name:'退出登录',exact:true}).click();await expect(page.getByRole('heading',{name:'登录工作台',exact:true})).toBeVisible();
});
