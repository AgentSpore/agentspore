'use client';

import { useState } from 'react';
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';
import { API_URL } from '@/lib/api';
import { claimWalletWithSignature, type ClaimResult } from '@/lib/solanaWalletClaim';

type Notice = Exclude<ClaimResult['kind'], 'ok'>;

const NOTICE_KEY = {
  no_provider: 'walletClaimNoProvider',
  wrong_wallet: 'walletClaimWrongWallet',
  cancelled: 'walletClaimCancelled',
  expired: 'walletClaimExpired',
  bad_signature: 'walletClaimBadSignature',
  rate_limited: 'walletClaimRateLimited',
  error: 'walletClaimError',
} as const satisfies Record<Notice, keyof typeof accountMessages.en>;

/** Shown when the pasted address belongs to another account: a wallet signature moves it here. */
export function WalletClaimOffer({ address, token, onClaimed }: { address: string; token: string; onClaimed: (address: string) => void }) {
  const tr = useTranslations(accountMessages);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  const confirm = async () => {
    setBusy(true);
    setNotice(null);
    const result = await claimWalletWithSignature({ apiUrl: API_URL, token, address });
    setBusy(false);
    if (result.kind === 'ok') onClaimed(address);
    else setNotice(result.kind);
  };

  return (
    <div className="mt-3 rounded-lg border border-amber-500/20 bg-amber-950/20 px-4 py-3 space-y-3">
      <p className="text-amber-200 text-xs leading-5">{tr('walletTaken')}</p>
      <button
        type="button"
        onClick={confirm}
        disabled={busy}
        aria-busy={busy}
        className="inline-flex min-h-11 items-center justify-center px-4 py-2 rounded-lg text-xs font-medium bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400"
      >
        {tr('walletClaimButton')}
      </button>
      <p role="status" aria-live="polite" className="text-neutral-300 text-xs leading-5 empty:hidden">
        {busy ? tr('walletClaimBusy') : notice ? tr(NOTICE_KEY[notice]) : ''}
      </p>
    </div>
  );
}
