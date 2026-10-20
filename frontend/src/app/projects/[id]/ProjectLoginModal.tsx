import { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react';
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';
import { getAuthSnapshot, subscribeAuthChanges } from '@/lib/auth';

type LoginError = 'invalidEmailOrPassword' | 'projectLoginVerify' | 'projectLoginWait' | 'projectLoginFailed' | 'connectionError';
type Tokens = { access_token: string; refresh_token: string };
type Session = Tokens & { userId: string };
type Request = (path: string, token?: string, options?: RequestInit) => Promise<Response>;
type Operation = { context: string; controller: AbortController };
const storageKeys = ['access_token', 'refresh_token', 'auth'] as const;
const getServerContext = () => '';
const nonempty = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;

function loginContext() {
  const { generation } = getAuthSnapshot();
  try { return JSON.stringify([generation, localStorage.getItem('auth')]); }
  catch { return JSON.stringify([generation, null]); }
}
function isTokenPair(value: unknown): value is Tokens {
  if (!value || typeof value !== 'object') return false;
  return storageKeys.slice(0, 2).every(key => nonempty(Reflect.get(value, key)));
}
async function readLoginResult(response: Response): Promise<Session | LoginError> {
  const errors: Partial<Record<number, LoginError>> = { 401: 'invalidEmailOrPassword', 403: 'projectLoginVerify', 429: 'projectLoginWait' };
  if (response.status !== 200) return errors[response.status] ?? 'projectLoginFailed';
  const value: unknown = await response.json();
  if (!isTokenPair(value)) return 'projectLoginFailed';
  // decode userId from JWT payload
  const encoded = value.access_token.split('.')[1] ?? '';
  const payload: unknown = JSON.parse(atob(encoded.replace(/-/g, '+').replace(/_/g, '/')));
  if (!payload || typeof payload !== 'object') return 'projectLoginFailed';
  const userId: unknown = Reflect.get(payload, 'sub');
  return nonempty(userId) ? { ...value, userId } : 'projectLoginFailed';
}
function saveLoginSession(session: Session, email: string) {
  const before = { access_token: localStorage.getItem('access_token'), refresh_token: localStorage.getItem('refresh_token'), auth: localStorage.getItem('auth') };
  const expected = { ...before }, values = { access_token: session.access_token, refresh_token: session.refresh_token,
    auth: JSON.stringify({ token: session.access_token, email, userId: session.userId }) };
  try {
    storageKeys.forEach(key => { localStorage.setItem(key, values[key]); expected[key] = values[key]; });
  } catch {
    if (storageKeys.every(key => localStorage.getItem(key) === expected[key])) {
      storageKeys.forEach(key => {
        const previous = before[key];
        if (previous === null) localStorage.removeItem(key); else localStorage.setItem(key, previous);
      });
    }
    throw new Error('Could not save project login session');
  }
}
function useLoginOperation(request: Request, email: string, password: string, onLogin: () => void) {
  const context = useSyncExternalStore(subscribeAuthChanges, loginContext, getServerContext);
  const pending = useRef<Operation | null>(null);
  const [state, setState] = useState<{ context: string; loading: boolean; error: LoginError | null } | null>(null);
  useEffect(() => () => { pending.current?.controller.abort(); pending.current = null; }, [context]);
  const owns = (operation: Operation | null) => operation !== null && pending.current === operation && operation.context === loginContext();
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (owns(pending.current) || context !== loginContext()) return;
    pending.current?.controller.abort();
    const operation = { context, controller: new AbortController() }; let received = false;
    pending.current = operation; setState({ context, loading: true, error: null });
    try {
      const response = await request('/auth/login', undefined, { method: 'POST', body: JSON.stringify({ email, password }), signal: operation.controller.signal });
      received = true;
      if (!owns(operation)) return;
      const result = await readLoginResult(response);
      if (!owns(operation)) return;
      if (typeof result === 'string') setState({ context, loading: false, error: result });
      else { saveLoginSession(result, email); onLogin(); }
    } catch {
      if (owns(operation)) setState({ context, loading: false, error: received ? 'projectLoginFailed' : 'connectionError' });
    } finally { if (pending.current === operation) pending.current = null; }
  };
  const current = state?.context === context;
  return { submit, loading: current && state.loading, error: current ? state.error : null };
}

/** Local project login dialog; only its current operation may install a session. */
export function ProjectLoginModal({ request, onLogin, onClose }: { request: Request; onLogin: () => void; onClose: () => void }) {
  const tr = useTranslations(publicMessages), shared = useTranslations(sharedMessages), id = useId();
  const [email, setEmail] = useState(''), [password, setPassword] = useState('');
  const state = useLoginOperation(request, email, password, onLogin);
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef(typeof document === 'undefined' ? null : document.activeElement);
  useEffect(() => {
    const element = dialog.current, previous = opener.current; element?.showModal();
    element?.querySelector<HTMLInputElement>('input[type="email"]')?.focus();
    return () => { element?.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  return (
    <dialog ref={dialog} aria-labelledby={`${id}-title`} onCancel={onClose}
      className="m-auto w-[calc(100%_-_2rem)] max-w-sm max-h-[calc(100dvh_-_2rem)] overflow-y-auto bg-[#0a0a0a] text-white border border-neutral-800/50 rounded-xl p-6 space-y-4 shadow-2xl backdrop:bg-black/70 backdrop:backdrop-blur-sm [overflow-wrap:anywhere]">
      <div className="flex items-center justify-between gap-3">
        <h2 id={`${id}-title`} className="min-w-0 font-medium text-sm">{tr('signInToContinue')}</h2>
        <button type="button" onClick={onClose} aria-label={shared('close')}
          className="min-h-11 min-w-11 shrink-0 text-neutral-400 text-xl focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-violet-300">x</button>
      </div>
      <form onSubmit={state.submit} className="space-y-3">
        <label htmlFor={`${id}-email`} className="block text-xs">{tr('email')}</label>
        <input id={`${id}-email`} type="email" autoComplete="email" autoFocus placeholder={tr('email')} value={email} onChange={event => setEmail(event.target.value)} required
          className="min-h-11 w-full min-w-0 bg-neutral-900/30 border border-neutral-800/50 rounded-lg px-3 py-2 text-sm font-mono focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-violet-300" />
        <label htmlFor={`${id}-password`} className="block text-xs">{tr('password')}</label>
        <input id={`${id}-password`} type="password" autoComplete="current-password" placeholder={tr('password')} value={password} onChange={event => setPassword(event.target.value)} required
          className="min-h-11 w-full min-w-0 bg-neutral-900/30 border border-neutral-800/50 rounded-lg px-3 py-2 text-sm font-mono focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-violet-300" />
        {state.error && <p role="alert" className="text-red-400 text-xs font-mono">{tr(state.error)}</p>}
        <button type="submit" disabled={state.loading}
          className="min-h-11 min-w-11 w-full bg-white text-black disabled:opacity-50 rounded-lg py-2 text-sm font-mono font-medium focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-violet-300">
          {state.loading ? tr('signingIn') : tr('signIn')}
        </button>
      </form>
    </dialog>
  );
}
