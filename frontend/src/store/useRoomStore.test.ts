import { describe, expect, it } from "vitest";
import { mergeMessages } from "./useRoomStore";
import type { PaymentMessage, TextMessage } from "../types/api";

const text: TextMessage = { id: "message-1", text: "Who paid the internet bill?", kind: "text", sender_id: 2,
  created_at: "2026-11-25T12:00:00Z" };
function proof(event: number, status: "pending" | "paid"): PaymentMessage {
  return { id: `proof-event:${event}`, event_id: event, kind: "payment_card", created_at: "2026-11-25T12:05:00Z",
    card: { proof_id: 9, debt_id: 3, debtor_id: 2, creditor_id: 1, status } };
}
describe("message reconciliation", () => {
  it("keeps an approval when an older pending event arrives late", () => {
    const paid = proof(30, "paid");
    expect(mergeMessages([paid, text], [proof(27, "pending")])).toEqual([text, paid]);
  });
  it("deduplicates a realtime message also received through history", () => {
    expect(mergeMessages([text], [text])).toEqual([text]);
  });
});
