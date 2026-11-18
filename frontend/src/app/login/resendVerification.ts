import { API_URL } from "@/lib/api";

/** Ask for a new verification email; the endpoint answers account-neutrally, so network failures are not surfaced either. */
export async function resendVerification(email: string): Promise<void> {
  try {
    await fetch(`${API_URL}/api/v1/auth/resend-verification`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.trim().toLowerCase() }),
    });
  } catch {
    // Keep the response account-neutral even when the request cannot reach the server.
  }
}
