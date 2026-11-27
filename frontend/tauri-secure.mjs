import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { deploymentURLs, securityPolicy } from "./build-security.mjs";

const [command, ...args] = process.argv.slice(2);
const release = ["build", "bundle", "release-check"].includes(command);
try {
  if (release || command === "dev") {
    if (args.some(arg => arg === "-c" || arg.startsWith("-c=") || arg.startsWith("--config"))) {
      throw new Error("Configure desktop URLs through the environment.");
    }
    const urls = deploymentURLs(release ? "production" : "development", release);
    if (command === "release-check") {
      console.log("Release API, WebSocket, and storage URLs use TLS.");
      process.exit(0);
    }
    const capability = JSON.parse(readFileSync(new URL("./src-tauri/capabilities/default.json", import.meta.url), "utf8"));
    delete capability.$schema;
    const scope = url => ({ url: `${url.origin}${url.pathname.replace(/\/+$/, "")}/**` });
    capability.permissions.push(
      { identifier: "http:default", allow: [scope(urls.VITE_API_URL), scope(urls.VITE_STORAGE_URL)] },
      { identifier: "opener:allow-open-url", allow: [scope(urls.VITE_STORAGE_URL)] },
    );
    capability.identifier = "desktop-configured";
    const security = { capabilities: [capability] };
    if (release) security.csp = `${securityPolicy(urls, true)}; frame-ancestors 'none'`;
    else security.devCsp = null; // The Vite server sends the nonce-based development policy.
    args.push("--config", JSON.stringify({ app: { security } }));
  }
  const cli = fileURLToPath(new URL("./node_modules/@tauri-apps/cli/tauri.js", import.meta.url));
  const result = spawnSync(process.execPath, command ? [cli, command, ...args] : [cli], {
    stdio: "inherit",
    env: { ...process.env, ...(release ? { SHARED_LIVING_RELEASE: "1", SHARED_LIVING_TAURI_BUILD: "1" } : { SHARED_LIVING_DESKTOP_DEV: "1" }) },
  });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
