import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { randomBytes } from "node:crypto";
import { deploymentURLs, securityPolicy } from "./build-security.mjs";

export default defineConfig(({ command, mode }) => {
  const release = process.env.SHARED_LIVING_RELEASE === "1";
  const urls = deploymentURLs(mode, release);
  const dev = command === "serve";
  const nonce = dev ? randomBytes(18).toString("base64") : undefined;
  if (!dev && Object.values(urls).some(url => ["http:", "ws:"].includes(url.protocol))) {
    console.warn("Development preview only: a loopback endpoint uses plaintext. Set HTTPS/WSS URLs for release.");
  }
  return {
    html: { cspNonce: nonce },
    plugins: [react(), {
      name: "shared-living-csp",
      apply: "build",
      transformIndexHtml() {
        // Tauri supplies its own CSP, including its IPC scheme.
        if (process.env.SHARED_LIVING_TAURI_BUILD === "1") return [];
        return [{ tag: "meta", injectTo: "head-prepend", attrs: {
          "http-equiv": "Content-Security-Policy", content: securityPolicy(urls),
        } }];
      },
    }],
    server: { host: "127.0.0.1", port: 1420, strictPort: true, headers: {
      "Content-Security-Policy": `${securityPolicy(urls, process.env.SHARED_LIVING_DESKTOP_DEV === "1", nonce)}; frame-ancestors 'none'`,
    } },
  };
});
