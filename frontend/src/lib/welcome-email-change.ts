export const EMAIL_CHANGE_STEPS = ['approve_old', 'confirm_new', 'done'] as const;
export type EmailChangeStep = (typeof EMAIL_CHANGE_STEPS)[number];

/**
 * Which stage an email-change reply reports. The machine `step` field wins; the message prefix is read only when an
 * older server sends no `step`, and an unknown reply is `confirm_new` or `done` by the caller's default.
 */
export async function readEmailChangeStep(response: Response, fallback: EmailChangeStep): Promise<EmailChangeStep> {
  const body: unknown = await response.json().catch(() => null);
  if (typeof body !== 'object' || body === null) return fallback;
  const step = 'step' in body ? EMAIL_CHANGE_STEPS.find(item => item === body.step) : undefined;
  if (step) return step;
  const message = 'message' in body ? body.message : '';
  return typeof message === 'string' && /^approve/i.test(message) ? 'approve_old' : fallback;
}
