export interface ConnectedAccount {
  id: number;
  label: string;
  email: string;
  provider: string;
  status: string;
  authSource: "browser-login";
  verifiedAt: number;
  hasCookies: true;
  authed?: boolean;
  isPro?: boolean;
  dailyUsed: number;
  totalGenerated: number;
  windowUsage?: number;
  windowCap?: number;
  cooldownRemaining?: number;
}

/** Metadata alone, pending sign-ins and synthetic profiles are not accounts.
 * Credentials stay in the worker's private SQLite database, never the browser.
 */
export function isConnectedAccount(value: unknown): value is ConnectedAccount {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return Number.isInteger(item.id) && Number(item.id) > 0 &&
    typeof item.email === "string" && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(item.email) &&
    item.authSource === "browser-login" && item.hasCookies === true &&
    typeof item.verifiedAt === "number" && Number.isFinite(item.verifiedAt) && item.verifiedAt > 0;
}

export const NO_ACCOUNTS_MESSAGE = "No accounts connected. Click [+ Add Gemini Account] to link your Google account.";
