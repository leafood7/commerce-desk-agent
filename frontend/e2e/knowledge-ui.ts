import {expect,type Page} from '@playwright/test';
export async function confirmKnowledgeImpact(page:Page,operation:'publish'|'withdraw'='publish'){
 const panel=page.getByRole('article',{name:'知识更新影响'});
 await expect(panel).toBeVisible();
 await panel.getByLabel('已了解受影响内容和待维护事项').check();
 await panel.getByRole('button',{name:operation==='withdraw'?'确认撤回当前政策':'确认发布此版本',exact:true}).click();
 await expect(panel).not.toBeVisible();
}
