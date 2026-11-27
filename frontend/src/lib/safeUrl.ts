export function safeDisplayUrl(value: string): URL | undefined {
  if (/^data:image\/(?:png|jpeg|webp);base64,[a-z0-9+/]+={0,2}$/i.test(value)) return new URL(value);
  try {
    const url = new URL(value);
    if (["https:", "http:"].includes(url.protocol) && !url.username && !url.password) return url;
  } catch { /* Invalid or relative URL. */ }
}

export function vietQrUrl(value: string): string | undefined {
  const url = safeDisplayUrl(value);
  return url?.origin === "https://img.vietqr.io" ? url.href : undefined;
}
