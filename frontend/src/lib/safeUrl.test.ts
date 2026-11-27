import { describe, expect, it } from "vitest";
import { safeDisplayUrl, vietQrUrl } from "./safeUrl";

describe("display URL policy", () => {
  it("allows absolute HTTP(S) URLs and supported raster data types", () => {
    expect(safeDisplayUrl("https://example.net/image")?.protocol).toBe("https:");
    expect(safeDisplayUrl("http://127.0.0.1:9000/image")?.protocol).toBe("http:");
    expect(safeDisplayUrl("data:image/png;base64,AAAA")?.protocol).toBe("data:");
  });

  it("rejects unsupported URL schemes, embedded credentials, and relative links", () => {
    for (const url of ["javascript:", "data:text/html;base64,AAAA", "data:image/svg+xml;base64,AAAA",
      "https://account@example.net/image", "//example.net/image"]) expect(safeDisplayUrl(url)).toBeUndefined();
    expect(vietQrUrl("https://img.vietqr.io/image/test")).toBeDefined();
    expect(vietQrUrl("https://example.net/image/test")).toBeUndefined();
  });
});
