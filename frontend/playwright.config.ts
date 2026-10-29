import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "../work/fullstack/playwright-artifacts",
  fullyParallel: false,
  workers: 1,
  use: { baseURL: process.env.WORKBENCH_WEB_URL || "http://127.0.0.1:5173",
    headless: true, trace: "off", screenshot: "only-on-failure" },
  reporter: [["list"]],
});
