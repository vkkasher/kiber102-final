import type { QueryResponse, SubscriptionOutput } from "./generated-api";
/** A display view of the API's deliberately untyped action dictionary. */
export type PendingActionView = { nonce: string; tool: "subscribe" | "save_feedback"; arguments: Record<string, unknown>; expires_at: string };
export function pendingAction(value: QueryResponse["pending_action"]): PendingActionView | null {
  if (!value || typeof value.nonce !== "string" || !/^[A-Za-z0-9_-]{16,100}$/.test(value.nonce) || !["subscribe", "save_feedback"].includes(String(value.tool)) || !value.arguments || typeof value.arguments !== "object" || Array.isArray(value.arguments) || typeof value.expires_at !== "string" || !Number.isFinite(Date.parse(value.expires_at))) return null;
  return { nonce: value.nonce, tool: value.tool as PendingActionView["tool"], arguments: value.arguments as Record<string, unknown>, expires_at: value.expires_at };
}
export function subscriptionCapability(value: QueryResponse["action_result"]): Pick<SubscriptionOutput, "id" | "delete_token"> | null {
  return value && typeof value.id === "string" && typeof value.delete_token === "string" && value.id && value.delete_token ? { id: value.id, delete_token: value.delete_token } : null;
}
