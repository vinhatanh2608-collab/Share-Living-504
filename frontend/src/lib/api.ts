import axios, { AxiosHeaders } from "axios";
import { isTauri } from "@tauri-apps/api/core";
import { fetch as nativeFetch } from "@tauri-apps/plugin-http";
import { openUrl } from "@tauri-apps/plugin-opener";
import type { Ticket } from "../types/api";
import { safeDisplayUrl } from "./safeUrl";

export const baseUrl = (import.meta.env.VITE_API_URL?.trim() || "http://127.0.0.1:8000/api/v1").replace(/\/+$/, "");
function defaultWsUrl() {
  const url = new URL("/ws", baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.href;
}
export function realtimeUrl() {
  return import.meta.env.VITE_WS_URL?.trim() || defaultWsUrl();
}
let access: string | null = null;
let unauthorized = () => {};
export function setAccess(token: string | null) { access = token; }
export function onUnauthorized(fn: () => void) { unauthorized = fn; }
const desktopFetch: typeof fetch = (input, init) => nativeFetch(input, {
  ...init, headers: init?.headers ?? (input instanceof Request ? input.headers : undefined),
  signal: init?.signal ?? (input instanceof Request ? input.signal : undefined),
  maxRedirections: 0, connectTimeout: 8000,
});
export const api = axios.create({ baseURL: baseUrl, timeout: 12000, allowAbsoluteUrls: false,
  ...(isTauri() ? { adapter: "fetch", env: { fetch: desktopFetch } } : {}),
});
api.interceptors.request.use(c => {
  const url = new URL(baseUrl);
  if (url.username || url.password || url.search || url.hash || !(url.protocol === "https:" ||
      (url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname)))) {
    throw new Error("Use an HTTPS API URL for a remote server.");
  }
  if (access && c.url !== "/auth/login" && c.url !== "/auth/register") c.headers.Authorization = `Bearer ${access}`;
  return c;
});
api.interceptors.response.use(r => r, error => {
  if (error.response?.status === 401 && access && error.config?.headers?.get("Authorization") === `Bearer ${access}`) unauthorized();
  return Promise.reject(error);
});
function serviceError(status?: number, retryAfter?: unknown) {
  if (status === 413) return "Tệp tải lên quá lớn. Hãy chọn ảnh nhỏ hơn hoặc nén ảnh rồi thử lại.";
  if (status === 429) {
    let seconds = 0;
    if (typeof retryAfter === "string" && retryAfter.trim()) {
      seconds = /^\d+$/.test(retryAfter.trim()) ? Number(retryAfter) : Math.ceil((Date.parse(retryAfter) - Date.now()) / 1000);
    }
    return Number.isFinite(seconds) && seconds > 0
      ? `Bạn thao tác quá nhanh. Vui lòng đợi ${seconds} giây rồi thử lại.`
      : "Bạn thao tác quá nhanh. Vui lòng đợi một lúc rồi thử lại.";
  }
  if (status === 503) return "Dịch vụ đang bận. Vui lòng thử lại sau ít phút.";
}
export function explain(error: unknown) {
  if (axios.isAxiosError(error)) {
    const headers = error.response?.headers;
    const retryAfter = headers instanceof AxiosHeaders ? headers.get("Retry-After")
      : Object.entries(headers ?? {}).find(([name]) => name.toLowerCase() === "retry-after")?.[1];
    const message = serviceError(error.response?.status, retryAfter);
    if (message) return message;
    const detail: unknown = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (error.response?.status === 422) return "Check the form values and try again.";
    if (!error.response) return "Could not reach the API. Check the connection and server URL.";
    return `Request failed (${error.response.status}).`;
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}

function safeObjectUrl(value: string) {
  const url = safeDisplayUrl(value);
  const storage = new URL(import.meta.env.VITE_STORAGE_URL?.trim() || "http://127.0.0.1:9000");
  if (!url || url.origin !== storage.origin || !url.pathname.startsWith(storage.pathname.replace(/\/+$/, "") + "/"))
    throw new Error("Untrusted file URL.");
  return url.href;
}
export async function upload(ticket: Ticket, file: Blob) {
  const headers = new Headers(ticket.headers);
  headers.delete("Content-Length"); // Both transports derive Content-Length from the Blob bytes.
  const fetcher = isTauri() ? desktopFetch : fetch;
  const result = await fetcher(safeObjectUrl(ticket.url), {
    method: "PUT", body: file, headers, credentials: "omit", redirect: "error",
    signal: AbortSignal.timeout(45000),
  });
  if (!result.ok) throw new Error(serviceError(result.status, result.headers.get("Retry-After")) ?? "Không thể tải ảnh lên. Vui lòng thử lại.");
}
export async function getObject(url: string) {
  const result = await (isTauri() ? desktopFetch : fetch)(safeObjectUrl(url),
    { credentials: "omit", redirect: "error", signal: AbortSignal.timeout(20000) });
  if (!result.ok) throw new Error(serviceError(result.status, result.headers.get("Retry-After")) ?? "Không thể tải ảnh biên nhận. Hãy mở lại để lấy liên kết mới.");
  return result.blob();
}
export async function openDownload(url: string) {
  const target = safeObjectUrl(url);
  if (isTauri()) await openUrl(target);
  else window.open(target, "_blank", "noopener,noreferrer");
}
