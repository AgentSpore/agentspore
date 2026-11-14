import { useEffect, useRef, useState, type FormEvent } from 'react';
import { API_URL } from '@/lib/api';
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { emailChangeMessages } from '@/lib/i18n/emailChange';

type Notice = 'sentNew' | 'sentOld' | 'wrongPassword' | 'needsProof' | 'rewardPending' | 'disposable' | 'cooldown' | 'signIn' | 'unavailable';
const SENT: Notice[] = ['sentNew', 'sentOld'];
const FIELD = 'min-h-11 w-full min-w-0 rounded-lg border border-neutral-600 bg-neutral-950 px-3 py-2 text-sm text-white';

async function detailOf(response: Response): Promise<string> {
  const body: unknown = await response.json().catch(() => null);
  const detail = typeof body === 'object' && body !== null && 'detail' in body ? body.detail : '';
  return typeof detail === 'string' ? detail.toLowerCase() : '';
}

async function failureNotice(response: Response): Promise<Notice> {
  const detail = await detailOf(response);
  if (response.status === 401) return 'signIn';
  if (response.status === 403) return detail.includes('password') ? 'wrongPassword' : 'needsProof';
  if (response.status === 409) return 'rewardPending';
  if (response.status === 422 && detail.includes('disposable')) return 'disposable';
  return response.status === 429 ? 'cooldown' : 'unavailable';
}

async function requestChange(token: string, email: string, password: string, signal: AbortSignal): Promise<Notice> {
  const response = await fetch(`${API_URL}/api/v1/users/me/email/change`, {
    method: 'POST', signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]),
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_email: email.trim(), current_password: password || null }),
  });
  if (response.status !== 202) return failureNotice(response);
  const body: unknown = await response.json().catch(() => null);
  const message = typeof body === 'object' && body !== null && 'message' in body ? body.message : '';
  return typeof message === 'string' && /^approve/i.test(message) ? 'sentOld' : 'sentNew';
}

/** The password field stays optional: accounts that sign in only with Google or GitHub have none. */
export function EmailChangeForm({ token }: { token: string }) {
  const tr = useTranslations(emailChangeMessages);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller; setBusy(true); setNotice(null);
    try {
      const result = await requestChange(token, email, password, controller.signal);
      if (controller.signal.aborted) return;
      setNotice(result);
      if (SENT.includes(result)) setPassword('');
    } catch {
      if (!controller.signal.aborted) setNotice('unavailable');
    } finally {
      if (!controller.signal.aborted) { pending.current = null; setBusy(false); }
    }
  }

  const sent = notice !== null && SENT.includes(notice);
  const failed = notice !== null && !sent;
  return <form onSubmit={event => void submit(event)} className="space-y-3">
    <p id="email-change-intro" className="text-sm text-neutral-300">{tr('formIntro')}</p>
    <label htmlFor="email-change-new" className="block text-sm text-neutral-100">{tr('newEmail')}</label>
    <input id="email-change-new" type="email" required autoComplete="email" value={email} disabled={busy}
      onChange={event => setEmail(event.target.value)} aria-describedby="email-change-intro" className={FIELD} />
    <label htmlFor="email-change-password" className="block text-sm text-neutral-100">{tr('password')}</label>
    <input id="email-change-password" type="password" autoComplete="current-password" value={password} disabled={busy}
      onChange={event => setPassword(event.target.value)} aria-invalid={notice === 'wrongPassword'}
      aria-describedby={`email-change-password-hint${notice ? ' email-change-notice' : ''}`} className={FIELD} />
    <p id="email-change-password-hint" className="text-xs text-neutral-300">{tr('passwordHint')}</p>
    <button type="submit" disabled={busy} className="min-h-11 rounded-lg border border-neutral-600 px-4 py-2 text-sm text-neutral-100 hover:bg-neutral-800 disabled:opacity-50">
      {tr(busy ? 'submitting' : 'submit')}
    </button>
    {notice && <p id="email-change-notice" role={failed ? 'alert' : 'status'} className={`text-sm ${failed ? 'text-red-300' : 'text-neutral-100'}`}>{tr(notice)}</p>}
  </form>;
}
