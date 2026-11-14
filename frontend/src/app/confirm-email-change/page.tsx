import type { Metadata } from 'next';
import { ConfirmEmailChange } from './ConfirmEmailChange';

export const metadata: Metadata = { robots: { index: false, follow: false }, referrer: 'no-referrer' };

export default function ConfirmEmailChangePage() {
  return <ConfirmEmailChange />;
}
