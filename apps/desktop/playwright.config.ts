import { defineConfig } from "@playwright/test";

// E2E against the real engine + UI. `tests/e2e/run.sh` starts a demo engine and Vite;
// in CI on Windows the same spec drives the Tauri app via its WebView debugging port.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:1420", screenshot: "only-on-failure", viewport: { width: 1440, height: 900 } },
  reporter: [["list"]],
});
