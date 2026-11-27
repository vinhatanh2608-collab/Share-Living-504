import { loadEnv } from "vite";

const defaults = {
  VITE_API_URL: "http://127.0.0.1:8000/api/v1",
  VITE_WS_URL: "ws://127.0.0.1:8000/ws",
  VITE_STORAGE_URL: "http://127.0.0.1:9000",
};

export function deploymentURLs(mode, release = false) {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const urls = {};
  for (const [name, fallback] of Object.entries(defaults)) {
    let url;
    try { url = new URL(env[name]?.trim() || fallback); }
    catch { throw new Error(`${name} must be an absolute URL.`); }
    const websocket = name === "VITE_WS_URL";
    const tls = websocket ? "wss:" : "https:";
    const local = websocket ? "ws:" : "http:";
    if (!/^(?:[a-z0-9.-]+|\[[0-9a-f:]+\])$/i.test(url.hostname) ||
        url.username || url.password || url.search || url.hash || !(url.protocol === tls ||
        (!release && url.protocol === local && ["127.0.0.1", "localhost"].includes(url.hostname)))) {
      throw new Error(`${name} must use ${tls}//${release ? " in a release build" : " for remote servers"}.`);
    }
    urls[name] = url;
  }
  return urls;
}

export function securityPolicy(urls, desktop = false, devNonce) {
  const api = urls.VITE_API_URL.origin;
  const ws = urls.VITE_WS_URL.origin;
  const storage = urls.VITE_STORAGE_URL.origin;
  const ipc = desktop ? " ipc: http://ipc.localhost https://ipc.localhost" : "";
  const nonce = devNonce ? ` 'nonce-${devNonce}'` : "";
  return [
    "default-src 'self'", `script-src 'self'${nonce}`, `style-src 'self'${nonce}`,
    `connect-src 'self'${ipc} ${api} ${ws} ${storage}`,
    `img-src 'self' blob: https://img.vietqr.io ${storage}`,
    "object-src 'none'", "base-uri 'none'", "form-action 'none'",
  ].join("; ");
}
