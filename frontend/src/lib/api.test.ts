import { AxiosError, AxiosHeaders } from "axios";
import { describe, expect, it } from "vitest";
import { explain } from "./api";

function failure(status: number, retryAfter?: string) {
  const config = { headers: new AxiosHeaders() };
  return new AxiosError("Request failed", undefined, config, undefined, {
    config, status, statusText: "", headers: retryAfter ? new AxiosHeaders({ "Retry-After": retryAfter }) : new AxiosHeaders(),
    data: { detail: "Technical server message" },
  });
}

describe("service errors", () => {
  it("explains an oversized upload in Vietnamese", () => {
    expect(explain(failure(413))).toContain("Tệp tải lên quá lớn");
  });

  it("uses Retry-After when available and gives generic guidance otherwise", () => {
    expect(explain(failure(429, "12"))).toContain("đợi 12 giây");
    expect(explain(failure(429))).toContain("đợi một lúc");
  });

  it("explains temporary service overload in Vietnamese", () => {
    expect(explain(failure(503))).toContain("Dịch vụ đang bận");
  });
});
