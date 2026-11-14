import { useEffect, useRef, useState } from 'react';
import { API_URL } from '@/lib/api';
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { welcomeRewardMessages } from '@/lib/i18n/welcomeReward';
import { WELCOME_FOCUS } from '@/components/welcome/focus';

type Notice = 'sent' | 'confirmed' | 'invalid' | 'expired' | 'cooldown' | 'unavailable' | 'signIn' | null;
const CODE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const BUTTON = `min-h-11 rounded-lg border border-neutral-600 px-4 py-2 text-sm text-neutral-100 hover:bg-neutral-800 disabled:opacity-50 ${WELCOME_FOCUS}`;

function needsValidCode(kind: 'request' | 'confirm', code: string) {
  return kind === 'confirm' && !CODE_PATTERN.test(code.trim());
}
function confirmationBody(kind: 'request' | 'confirm', code: string) {
  return kind === 'confirm' ? { body: JSON.stringify({ code: code.trim() }) } : {};
}
function errorNotice(status: number, kind: 'request' | 'confirm'): Notice {
  if (status === 401) return 'signIn';
  if (status === 429) return 'cooldown';
  return kind === 'confirm' && [400, 409, 410, 422].includes(status) ? 'expired' : 'unavailable';
}
function invalidCode(notice: Notice) { return notice === 'invalid' || notice === 'expired'; }
function failedNotice(notice: Notice) { return notice !== null && notice !== 'sent' && notice !== 'confirmed'; }

/** The owner-keyed profile unmount aborts requests and removes the unsaved code. */
export function WelcomeEmailProof({ token, onConfirmed }: { token: string; onConfirmed: () => void }) {
  const tr = useTranslations(welcomeRewardMessages);
  const [code, setCode] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState<'request' | 'confirm' | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  const failed = failedNotice(notice);

  async function submit(kind: 'request' | 'confirm') {
    if (pending.current) return;
    if (needsValidCode(kind, code)) { setNotice('invalid'); return; }
    const controller = new AbortController();
    pending.current = controller;
    setBusy(kind); setNotice(null);
    try {
      const response = await fetch(`${API_URL}/api/v1/auth/email-proof/${kind}`, {
        method: 'POST', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]),
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        ...confirmationBody(kind, code),
      });
      if (controller.signal.aborted) return;
      if (response.status !== (kind === 'request' ? 202 : 200)) {
        setNotice(errorNotice(response.status, kind));
      } else if (kind === 'request') { setSent(true); setNotice('sent'); }
      else { setCode(''); setNotice('confirmed'); onConfirmed(); }
    } catch {
      if (!controller.signal.aborted) setNotice('unavailable');
    } finally {
      if (!controller.signal.aborted) { pending.current = null; setBusy(null); }
    }
  }

  return <div className="mt-4 space-y-3">
    <button type="button" className={BUTTON} disabled={busy !== null} onClick={() => void submit('request')}>
      {busy === 'request' ? tr('sending') : tr(sent ? 'resend' : 'send')}
    </button>
    {sent && <form className="space-y-3" onSubmit={event => { event.preventDefault(); void submit('confirm'); }}>
      <label className="block text-sm text-neutral-100" htmlFor="welcome-email-code">{tr('code')}</label>
      <p id="welcome-code-hint" className="text-sm text-neutral-300">{tr('codeHint')}</p>
      <input id="welcome-email-code" value={code} onChange={event => setCode(event.target.value)}
        autoComplete="one-time-code" autoCapitalize="none" spellCheck={false} required maxLength={43} minLength={43}
        pattern="[A-Za-z0-9_-]{43}" aria-describedby={`welcome-code-hint${failed ? ' welcome-code-notice' : ''}`}
        aria-invalid={invalidCode(notice)} disabled={busy !== null}
        className={`min-h-11 w-full min-w-0 rounded-lg border border-neutral-600 bg-neutral-950 px-3 py-2 font-mono text-sm text-white ${WELCOME_FOCUS}`} />
      <button type="submit" className={BUTTON} disabled={busy !== null}>
        {busy === 'confirm' ? tr('confirming') : tr('confirm')}
      </button>
    </form>}
    {notice && <p id="welcome-code-notice" role={failed ? 'alert' : 'status'} className={`text-sm ${failed ? 'text-red-300' : 'text-neutral-200'}`}>{tr(notice)}</p>}
  </div>;
}
