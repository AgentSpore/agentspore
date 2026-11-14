import { useEffect, useRef, useState } from 'react';
import { API_URL } from '@/lib/api';
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { welcomeRewardMessages } from '@/lib/i18n/welcomeReward';
import { decodeWelcomeReward, type WelcomeRewardStatus } from '@/lib/welcome-reward';
import { WELCOME_FOCUS } from '@/components/welcome/focus';

/** Explicit recovery never turns a profile read into a reward mutation. */
export function WelcomeApplication({ token, onApplied }: { token: string; onApplied: (value: WelcomeRewardStatus) => void }) {
  const tr = useTranslations(welcomeRewardMessages);
  const pending = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => () => pending.current?.abort(), []);
  async function apply() {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller; setBusy(true); setFailed(false);
    try {
      const response = await fetch(`${API_URL}/api/v1/users/me/rewards/welcome/application`, {
        method: 'POST', headers: { Authorization: `Bearer ${token}` },
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]),
      });
      if (!response.ok) throw new Error('Application unavailable');
      const data = decodeWelcomeReward(await response.json());
      if (!controller.signal.aborted) onApplied(data);
    } catch {
      if (!controller.signal.aborted) setFailed(true);
    } finally {
      if (!controller.signal.aborted) { pending.current = null; setBusy(false); }
    }
  }
  return <div className="space-y-3">
    <button type="button" disabled={busy} onClick={() => void apply()} className={`min-h-11 rounded-lg border border-neutral-600 px-4 py-2 text-sm text-neutral-100 hover:bg-neutral-800 disabled:opacity-50 ${WELCOME_FOCUS}`}>{tr(busy ? 'applying' : 'apply')}</button>
    {failed && <p role="alert" className="text-sm text-red-300">{tr('applyFailed')}</p>}
  </div>;
}
