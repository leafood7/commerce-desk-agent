import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

function password() {
  if (process.env.WORKBENCH_TEST_PASSWORD) return process.env.WORKBENCH_TEST_PASSWORD;
  return JSON.parse(readFileSync(new URL("../../work/fullstack/local-login.json", import.meta.url), "utf-8")).password as string;
}

async function login(page: Page, organization = "demo-a") {
  await page.goto("/");
  await page.getByLabel("商家编号").fill(organization);
  await page.getByLabel("邮箱").fill("support@demo.test");
  await page.getByLabel("密码").fill(password());
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("heading", { name: "订单助手" })).toBeVisible();
}

test("login, MySQL lookup, SSE steps and logout", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "查询订单" }).click();
  await expect(page.getByText("已取得订单信息", { exact: true })).toBeVisible();
  await expect(page.locator("article")).toContainText("订单 A1001");
  await expect(page.locator("article")).toContainText("实际签收时间未提供");
  await page.screenshot({ path: "../work/fullstack/workbench-preview.png", fullPage: true });
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByRole("heading", { name: "登录工作台" })).toBeVisible();
});

test("another organization cannot query the first merchant's order", async ({ page }) => {
  await login(page, "demo-b");
  await page.getByLabel("订单号").fill("A1001");
  await page.getByRole("button", { name: "查询订单" }).click();
  await expect(page.getByRole("alert")).toContainText("未找到当前商家的订单或记录");
  await page.getByLabel("订单号").fill("B2001");
  await page.getByRole("button", { name: "查询订单" }).click();
  await expect(page.locator("article")).toContainText("订单 B2001");
});

test("a broken event subscription retries the same run", async ({ page }) => {
  await login(page);
  let creations = 0, subscriptions = 0;
  page.on("request", request => {
    if (request.method() === "POST" && request.url().endsWith("/api/v1/runs")) creations++;
  });
  await page.route("**/api/v1/runs/*/events", async route => {
    subscriptions++;
    if (subscriptions === 1) await route.abort();
    else await route.continue();
  });
  await page.getByRole("button", { name: "查询订单" }).click();
  await expect(page.locator("article")).toContainText("订单 A1001");
  expect(creations).toBe(1);
  expect(subscriptions).toBeGreaterThan(1);
});

test("CRLF event streams publish the same real order result", async ({page}) => {
  await login(page);
  await page.route("**/api/v1/runs/*/events", async route => {
    const response = await route.fetch();
    await route.fulfill({response,body:(await response.text()).replace(/\n/g,"\r\n")});
  });
  await page.getByRole("button",{name:"查询订单"}).click();
  await expect(page.locator("article")).toContainText("订单 A1001");
});

test("early EOF resumes with the cursor and never creates another run", async ({page}) => {
  await login(page);
  let subscriptions = 0, creations = 0, firstId = "", resumedCursor = "";
  let forceRunning = true;
  page.on("request", request => {
    if (request.method() === "POST" && request.url().endsWith("/api/v1/runs")) creations++;
  });
  await page.route(/\/api\/v1\/runs\/[^/]+$/, async route => {
    const response = await route.fetch();
    if (route.request().method() === "GET" && forceRunning) {
      forceRunning = false;
      await route.fulfill({response,json:{...(await response.json()),status:"running"}});
    } else await route.fulfill({response});
  });
  await page.route("**/api/v1/runs/*/events", async route => {
    subscriptions++;
    if (subscriptions === 1) {
      const response = await route.fetch();
      const firstBlock = (await response.text()).split("\n\n")[0];
      firstId = firstBlock.match(/^id: (\d+)/m)?.[1] || "";
      await route.fulfill({response,body:firstBlock+"\n\n"});
    } else {
      resumedCursor = route.request().headers()["last-event-id"] || "";
      await route.continue();
    }
  });
  await page.getByRole("button",{name:"查询订单"}).click();
  await expect(page.locator("article")).toContainText("订单 A1001");
  expect(creations).toBe(1);
  expect(subscriptions).toBe(2);
  expect(firstId).not.toBe("");
  expect(resumedCursor).toBe(firstId);
});

test("expired stream access stops subscriptions and asks for login", async ({page}) => {
  await login(page);
  let subscriptions = 0;
  await page.route("**/api/v1/runs/*/events", async route => {
    subscriptions++;
    await route.fulfill({status:401,body:"{}"});
  });
  await page.getByRole("button",{name:"查询订单"}).click();
  await expect(page.getByRole("alert")).toContainText("登录已失效");
  expect(subscriptions).toBe(1);
});

test("runtime panel shows real safe settings without offering permission overrides",async ({page})=>{
  await login(page);
  await page.getByRole("button",{name:"运行策略",exact:true}).click();
  await expect(page.getByRole("heading",{name:"运行策略"})).toBeVisible();
  await expect(page.locator("article").first()).toContainText("默认不调用模型");
  await expect(page.locator("article").first()).toContainText("10次");
  await expect(page.locator("article").first()).toContainText("不自动重试");
  await expect(page.getByRole("heading",{name:"我的执行队列"})).toBeVisible();
});
