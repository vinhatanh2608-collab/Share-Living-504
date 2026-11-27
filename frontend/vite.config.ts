import { defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { deploymentURLs, securityPolicy } from "./build-security.mjs";

export default defineConfig(({ command, mode }) => {
  const release = process.env.SHARED_LIVING_RELEASE === "1";
  const urls = deploymentURLs(mode, release);
  const dev = command === "serve";
  const nonce = dev ? randomBytes(18).toString("base64") : undefined;
  if (!dev && Object.values(urls).some(url => ["http:", "ws:"].includes(url.protocol))) {
    console.warn("Development preview only: a loopback endpoint uses plaintext. Set HTTPS/WSS URLs for release.");
  }
  const publicHost = process.env.SHARED_LIVING_PUBLIC_HOST;
  if (publicHost && !/^[a-z0-9-]+\.trycloudflare\.com$/.test(publicHost)) {
    throw new Error("Invalid Cloudflare Quick Tunnel hostname");
  }
  const proxy: Record<string, ProxyOptions> = {
    "/api": { target: "http://127.0.0.1:8000" },
    "^/ws(?:[?]|$)": { target: "ws://127.0.0.1:8000", ws: true },

  };
  const receiptKey = process.env.SHARED_LIVING_RECEIPT_PROXY_KEY;
  const receiptPrefix = /^\/__receipt_storage\/(\d+)\.([a-f0-9]{64})(\/[^?]+\?.+)$/;
  if (publicHost && receiptKey) {
    proxy["^/__receipt_storage/"] = {
      target: "http://127.0.0.1:9000",
      changeOrigin: true,
      rewrite: path => receiptPrefix.exec(path)?.[3] ?? "/",
      bypass(request) {
        const match = receiptPrefix.exec(request.url ?? "");
        if (!match || !["GET", "PUT"].includes(request.method ?? "")) return false;
        const [, expires, hex, target] = match;
        if (Number(expires) <= Date.now() / 1000) return false;
        const expected = createHmac("sha256", receiptKey)
          .update(request.method + " " + target + " " + expires).digest();
        if (!timingSafeEqual(expected, Buffer.from(hex, "hex"))) return false;
        if (request.method === "PUT") {
          const length = Number(request.headers["content-length"]);
          if (!Number.isInteger(length) || length < 1 || length > 10 * 1024 * 1024) return false;
        }
      },
    };
  }
  if (publicHost) {
    proxy["^/ws(?:[?]|$)"].configure = server => {
      server.on("proxyReqWs", (upstream, request) => {
        if (request.headers.host === publicHost && request.headers.origin === "https://" + publicHost) {
          upstream.setHeader("Origin", "http://127.0.0.1:1420");
        }
      });
    };
  }
  const network = { host: "127.0.0.1", port: 1420, strictPort: true, proxy,
    allowedHosts: publicHost ? [publicHost] : [] };
  const policy = (value: string) => publicHost
    ? value.replace("connect-src 'self'", "connect-src 'self' wss://" + publicHost)
    : value;
  return {
    html: { cspNonce: nonce },
    plugins: [react(), {
      name: "shared-living-csp",
      apply: "build",
      transformIndexHtml() {
        // Tauri supplies its own CSP, including its IPC scheme.
        if (process.env.SHARED_LIVING_TAURI_BUILD === "1") return [];
        return [{ tag: "meta", injectTo: "head-prepend", attrs: {
          "http-equiv": "Content-Security-Policy", content: policy(securityPolicy(urls)),
        } }];
      },
    }],
    preview: network,
    server: { ...network, headers: {
      "Content-Security-Policy": `${policy(securityPolicy(urls, process.env.SHARED_LIVING_DESKTOP_DEV === "1", nonce))}; frame-ancestors 'none'`,
    } },
  };
});
