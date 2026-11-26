import { isTauri } from "@tauri-apps/api/core";
import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";

export async function enableAlerts() {
  if (isTauri()) return await isPermissionGranted() || (await requestPermission()) === "granted";
  return typeof Notification !== "undefined" && (await Notification.requestPermission()) === "granted";
}
export async function notify(title: string, body: string) {
  try {
    if (isTauri()) {
      if (await isPermissionGranted()) sendNotification({ title, body });
    } else if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      new Notification(title, { body });
    }
  } catch { /* realtime state does not depend on OS delivery */ }
}
