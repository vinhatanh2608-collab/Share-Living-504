import { describe, expect, it } from "vitest";
import { localDate, realtimeUpdate } from "./living";

describe("Shared Living desktop contract", () => {
  it("uses the same household day as the backend across the UTC boundary", () => {
    expect(localDate(new Date("2026-01-01T16:59:00Z"))).toBe("2026-01-01");
    expect(localDate(new Date("2026-01-01T17:00:00Z"))).toBe("2026-01-02");
  });

  it.each(["group_join_requested", "group_membership_updated", "group_debt_reminder",
    "group_proof_submitted", "PROOF_VERIFIED"])("refreshes and surfaces %s", type => {
    expect(realtimeUpdate({ type, group_id: 1 })?.notice).toEqual(expect.any(String));
  });

  it("refreshes after chat or reconnect and ignores heartbeats and malformed events", () => {
    for (const type of ["group_chat", "connected", "resync"]) expect(realtimeUpdate({ type })).toEqual({ notice: undefined });
    for (const event of [null, {}, { type: "heartbeat" }, { type: "constructor" }]) expect(realtimeUpdate(event)).toBeUndefined();
  });
});
