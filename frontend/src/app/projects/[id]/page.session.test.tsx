// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { renderToString } from 'react-dom/server';
import ProjectPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { sharedMessages } from '@/lib/i18n/shared';

const route = vi.hoisted(() => ({ id: 'demo-project' }));
// Next navigation is the browser routing boundary; Header and auth subscriptions run unchanged.
vi.mock('next/navigation', () => ({ useParams: () => route, usePathname: () => `/projects/${route.id}`, useRouter: () => ({ refresh: vi.fn() }) }));
const originalScroll = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView');
beforeEach(() => {
  route.id = 'demo-project'; localStorage.clear();
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, writable: true, value: vi.fn() });
});
afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
  if (originalScroll) Object.defineProperty(Element.prototype, 'scrollIntoView', originalScroll);
  else Reflect.deleteProperty(Element.prototype, 'scrollIntoView');
});

function projectButton(name: RegExp) {
  const buttons = screen.getAllByRole('button', { name }).filter(button => !button.closest('header'));
  expect(buttons).toHaveLength(1);
  return buttons[0];
}

function mockProject(status: string, deployUrl: string | null) {
  const project = {
    id: "demo-project", title: "Example Project", description: "Synthetic demo fixture",
    category: "tools", status, repo_url: "https://github.com/Example/demo",
    deploy_url: deployUrl, tech_stack: [], agent_name: "Demo", agent_handle: "demo",
    creator_agent_id: "demo-agent", votes_up: 0, votes_down: 0,
    created_at: "2026-10-01T00:00:00Z",
  };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (input.endsWith("/auth/me") && new Headers(init?.headers).get("Authorization") === "Bearer malformed") return new Response("{}", { status: 401 });
    const body = input.endsWith("/auth/me") ? { id: "synthetic-user", email: "oauth@example.invalid", name: "Synthetic account", avatar_url: null, token_balance: 0, is_admin: false }
      : input.endsWith("/ownership") ? null
      : input.endsWith("/contributors") ? { contributors: [] } : project;
    return new Response(JSON.stringify(body), { status: 200 });
  }));
}

it('reacts to legacy sign-in and sign-out storage changes without remounting', async () => {
  mockProject('active', null);
  render(<ProjectPage />);
  await screen.findByRole('heading', { name: 'Example Project' });
  act(() => {
    localStorage.setItem('auth', JSON.stringify({ token: 'synthetic-session', email: 'synthetic@example.invalid', userId: 'synthetic-user' }));
    window.dispatchEvent(new Event('storage'));
  });
  await screen.findByText('synthetic@example.invalid');
  act(() => { localStorage.removeItem('auth'); window.dispatchEvent(new Event('storage')); });
  await waitFor(() => expect(screen.queryByText('synthetic@example.invalid')).toBeNull());
});

it('ignores malformed legacy storage and updates from an OAuth session', async () => {
  localStorage.setItem('auth', '{malformed');
  const payload = btoa(JSON.stringify({ sub: 'synthetic-user', email: 'synthetic@example.invalid' }));
  localStorage.setItem('access_token', `synthetic.${payload}.fixture`);
  mockProject('active', null);
  render(<ProjectPage />);
  await screen.findByText('synthetic@example.invalid');
});

it('does not show the previous project while the next project is loading', async () => {
  mockProject('active', null);
  const view = render(<ProjectPage />);
  await screen.findByRole('heading', { name: 'Example Project' });
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})));
  route.id = 'next-project';
  view.rerender(<ProjectPage />);
  expect(screen.queryByRole('heading', { name: 'Example Project' })).toBeNull();
});

it('discards a chat response after leaving the chat tab', async () => {
  mockProject('active', null);
  render(<ProjectPage />);
  await screen.findByRole('heading', { name: 'Example Project' });
  let resolve: (response: Response) => void = () => {};
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(done => { resolve = done; })));
  fireEvent.click(screen.getByRole('button', { name: /discussion/i }));
  fireEvent.click(screen.getByRole('button', { name: /overview/i }));
  await act(async () => { resolve(new Response(JSON.stringify([{ id: 'stale', sender_name: 'Synthetic', sender_type: 'human', sender_handle: null, content: 'Stale chat', message_type: 'text', created_at: '2026-10-19T00:00:00Z' }]))); });
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})));
  fireEvent.click(screen.getByRole('button', { name: /discussion/i }));
  expect(screen.queryByText('Stale chat')).toBeNull();
});


it('signs out of both legacy and OAuth sessions', async () => {
  localStorage.setItem('auth', JSON.stringify({ token: 'synthetic-session', email: 'synthetic@example.invalid', userId: 'synthetic-user' }));
  localStorage.setItem('access_token', `synthetic.${btoa(JSON.stringify({ sub: 'synthetic-user' }))}.fixture`);
  localStorage.setItem('refresh_token', 'synthetic-refresh');
  mockProject('active', null);
  render(<ProjectPage />);
  await screen.findByText('synthetic@example.invalid');
  fireEvent.click(projectButton(/sign out/i));
  expect(localStorage.getItem('auth')).toBeNull();
  expect(localStorage.getItem('access_token')).toBeNull();
  expect(localStorage.getItem('refresh_token')).toBeNull();
});


it('does not refresh chat from a send completed after sign-out', async () => {
  localStorage.setItem('auth', JSON.stringify({ token: 'synthetic-session', email: 'synthetic@example.invalid', userId: 'synthetic-user' }));
  mockProject('active', null);
  render(<ProjectPage />);
  await screen.findByText('synthetic@example.invalid');
  let finish: (response: Response) => void = () => {};
  const requests = vi.fn((input: string, init?: RequestInit) => init?.method === 'POST'
    ? new Promise<Response>(resolve => { finish = resolve; })
    : Promise.resolve(new Response(JSON.stringify(input.includes('/messages') ? [] : { contributors: [] }))));
  vi.stubGlobal('fetch', requests);
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  fireEvent.click(screen.getByRole('button', { name: /discussion/i }));
  await act(async () => {});
  await act(async () => vi.advanceTimersByTimeAsync(100));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Synthetic message' } });
  const form = screen.getByRole('textbox').closest('form');
  if (!form) throw new Error('Missing chat form');
  fireEvent.submit(form);
  fireEvent.click(projectButton(/sign out/i));
  await act(async () => {});
  const afterLogout = requests.mock.calls.length;
  await act(async () => { finish(new Response('{}')); });
  expect(requests).toHaveBeenCalledTimes(afterLogout);
});

it.each(['legacy', 'oauth', 'denied'] as const)('keeps %s storage safe during mount and later events', async mode => {
  mockProject('active', null);
  if (mode === 'legacy') localStorage.setItem('auth', JSON.stringify({ token: 1, email: null, userId: [] }));
  if (mode === 'oauth') localStorage.setItem('access_token', 'malformed');
  if (mode === 'denied') vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('Denied', 'SecurityError'); });
  render(<ProjectPage />);
  await screen.findByRole('heading', { name: 'Example Project' });
  act(() => window.dispatchEvent(new Event('storage')));
  expect(projectButton(/sign in/i)).toBeTruthy();
  for (const [input, init] of vi.mocked(fetch).mock.calls) {
    if (String(input).endsWith('/contributors')) expect(new Headers(init?.headers).has('Authorization')).toBe(false);
  }
});


it('does not read browser authentication during server rendering', () => {
  const read = vi.spyOn(Storage.prototype, 'getItem');
  expect(renderToString(<ProjectPage />)).toContain('Loading...');
  expect(read).not.toHaveBeenCalled();
});

it('does not clear a newer OAuth session installed while signing out', async () => {
  localStorage.setItem('auth', JSON.stringify({ token: 'synthetic-old', email: 'old@example.invalid', userId: 'old-user' }));
  localStorage.setItem('access_token', 'synthetic-old');
  localStorage.setItem('refresh_token', 'synthetic-old-refresh');
  mockProject('active', null);
  render(<ProjectPage />);
  await screen.findByText('old@example.invalid');
  const remove = Storage.prototype.removeItem;
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(function (this: Storage, key: string) {
    remove.call(this, key);
    if (key === 'auth') {
      this.setItem('access_token', `synthetic.${btoa(JSON.stringify({ sub: 'new-user', email: 'new@example.invalid' }))}.fixture`);
      this.setItem('refresh_token', 'synthetic-new-refresh');
    }
  });
  fireEvent.click(projectButton(/sign out/i));
  await screen.findByText('new@example.invalid');
  expect(localStorage.getItem('refresh_token')).toBe('synthetic-new-refresh');
});


it('ignores a late project response after navigation', async () => {
  let finish: (response: Response) => void = () => {};
  const project = { id: 'next-project', title: 'Next project', description: '', category: 'tools', status: 'active', repo_url: null, deploy_url: null, tech_stack: [], agent_name: 'Demo', agent_handle: 'demo', creator_agent_id: 'demo-agent', votes_up: 0, votes_down: 0, created_at: '2026-10-01T00:00:00Z' };
  vi.stubGlobal('fetch', vi.fn((input: string) => {
    if (input.endsWith('/demo-project')) return new Promise<Response>(resolve => { finish = resolve; });
    return Promise.resolve(new Response(JSON.stringify(input.endsWith('/ownership') ? null : input.endsWith('/contributors') ? { contributors: [] } : project)));
  }));
  const view = render(<ProjectPage />);
  route.id = 'next-project';
  view.rerender(<ProjectPage />);
  await screen.findByRole('heading', { name: 'Next project' });
  await act(async () => { finish(new Response(JSON.stringify({ ...project, id: 'demo-project', title: 'Stale project' }))); });
  expect(screen.getByRole('heading', { name: 'Next project' })).toBeTruthy();
  expect(screen.queryByText('Stale project')).toBeNull();
});

const PROJECT_SECURITY_OWNER = {
  id: "account-a", name: "Shared name", email: "owner@example.invalid",
  avatar_url: null, token_balance: 0, is_admin: false,
};
const PROJECT_SECURITY_OTHER = { ...PROJECT_SECURITY_OWNER, id: "account-b" };
const PROJECT_SECURITY_ROWS = [
  { id: "project-security-a", sender_user_id: PROJECT_SECURITY_OWNER.id },
  { id: "project-security-b", sender_user_id: PROJECT_SECURITY_OTHER.id },
  { id: "project-security-null", sender_user_id: null },
  { id: "project-security-missing" },
].map(author => ({ ...author, sender_name: PROJECT_SECURITY_OWNER.name, sender_handle: null,
  sender_type: "human", content: author.id, message_type: "text", created_at: "2026-10-19T00:00:00Z" }));

function projectSecuritySession(token: string | null) {
  if (token) {
    localStorage.setItem("access_token", token);
    localStorage.setItem("auth", JSON.stringify({ token, email: "Shared name@example.invalid", userId: "account-b" }));
  } else {
    localStorage.removeItem("auth");
    localStorage.removeItem("access_token");
  }
  window.dispatchEvent(new Event("storage"));
}

function pendingSecurityProfile() {
  let resolve: (response: Response) => void = () => { throw new Error("Profile promise not initialized"); };
  let reject: (error: Error) => void = () => { throw new Error("Profile promise not initialized"); };
  const promise = new Promise<Response>((complete, fail) => { resolve = complete; reject = fail; });
  return { promise, resolve, reject };
}

function stubProjectSecurity(profile: (authorization: string | null) => Promise<Response>) {
  mockProject("active", null);
  const projectFetch = fetch;
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => {
    if (input.endsWith("/auth/me")) return profile(new Headers(init?.headers).get("Authorization"));
    if (input.includes("/chat/project/demo-project/messages")) return Promise.resolve(Response.json([...PROJECT_SECURITY_ROWS].reverse()));
    return projectFetch(input, init);
  }));
}

async function openProjectSecurity() {
  render(<ProjectPage />);
  await screen.findByRole("heading", { name: "Example Project" });
  fireEvent.click(screen.getByRole("button", { name: /discussion/i }));
  await screen.findByText("project-security-a");
}

function projectSecurityControls(id: string, owned: boolean, locale: "en" | "ru" = "en") {
  const row = screen.getByText(id).closest(".group");
  if (!(row instanceof HTMLElement)) throw new Error("Project security message row missing");
  for (const title of locale === "ru" ? ["Изменить", "Удалить"] : ["Edit", "Delete"]) expect(within(row).queryAllByTitle(title)).toHaveLength(owned ? 1 : 0);
}

it.each(["Shared name", "Renamed account"])("SEC.CHAT project grants only confirmed profile ID with name %s", async name => {
  projectSecuritySession("synthetic-security-a");
  stubProjectSecurity(async () => Response.json({ ...PROJECT_SECURITY_OWNER, name }));
  await openProjectSecurity();
  await waitFor(() => projectSecurityControls("project-security-a", true));
  for (const id of ["project-security-b", "project-security-null", "project-security-missing"]) projectSecurityControls(id, false);
  const panel = screen.getByText("project-security-a").closest(".chat-panel");
  if (!(panel instanceof HTMLElement)) throw new Error("Project chat panel missing");
  expect(within(panel).getAllByText(PROJECT_SECURITY_OWNER.name)).toHaveLength(4);
});

it("SEC.CHAT project removes privileges until replacement profile confirms its ID", async () => {
  const replacement = pendingSecurityProfile();
  projectSecuritySession("synthetic-security-a");
  stubProjectSecurity(authorization => authorization === "Bearer synthetic-security-a"
    ? Promise.resolve(Response.json(PROJECT_SECURITY_OWNER)) : replacement.promise.then(response => response.clone()));
  await openProjectSecurity();
  await waitFor(() => projectSecurityControls("project-security-a", true));
  await act(async () => projectSecuritySession("synthetic-security-b"));
  projectSecurityControls("project-security-a", false);
  projectSecurityControls("project-security-b", false);
  await act(async () => replacement.resolve(Response.json(PROJECT_SECURITY_OTHER)));
  await waitFor(() => projectSecurityControls("project-security-b", true));
  projectSecurityControls("project-security-a", false);
  await act(async () => projectSecuritySession(null));
  projectSecurityControls("project-security-b", false);
});

it.each(["switch", "logout"])("SEC.CHAT project rejects stale profile and locally claimed ID after %s", async transition => {
  const stale = pendingSecurityProfile();
  projectSecuritySession("synthetic-security-a");
  const profiles = vi.fn((authorization: string | null) => authorization === "Bearer synthetic-security-a"
    ? stale.promise.then(response => response.clone()) : Promise.resolve(Response.json(PROJECT_SECURITY_OTHER)));
  stubProjectSecurity(profiles);
  await openProjectSecurity();
  await waitFor(() => expect(profiles).toHaveBeenCalled());
  projectSecurityControls("project-security-a", false);
  projectSecurityControls("project-security-b", false);
  await act(async () => projectSecuritySession(transition === "switch" ? "synthetic-security-b" : null));
  if (transition === "switch") await waitFor(() => projectSecurityControls("project-security-b", true));
  await act(async () => stale.resolve(Response.json(PROJECT_SECURITY_OWNER)));
  projectSecurityControls("project-security-a", false);
  projectSecurityControls("project-security-b", transition === "switch");
});

async function openProjectIdentityComposer() {
  const pending = pendingSecurityProfile();
  const writes: { method: string; path: string; authorization: string | null; content: string }[] = [];
  projectSecuritySession("synthetic-security-a");
  stubProjectSecurity(authorization => authorization === "Bearer synthetic-security-a"
    ? Promise.resolve(Response.json(PROJECT_SECURITY_OWNER)) : pending.promise.then(response => response.clone()));
  const read = fetch;
  vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => {
    if (init?.method === "POST" || init?.method === "PATCH") {
      writes.push({ method: init.method, path: new URL(input).pathname,
        authorization: new Headers(init.headers).get("Authorization"), content: JSON.parse(String(init.body)).content });
      return Promise.resolve(Response.json({ status: "ok" }));
    }
    return read(input, init);
  }));
  await openProjectSecurity();
  await waitFor(() => projectSecurityControls("project-security-a", true));
  return { pending, writes, input: screen.getByRole<HTMLTextAreaElement>("textbox") };
}

it("SEC.CHAT project composer clears A editor after confirmed B without foreign PATCH", async () => {
  const chat = await openProjectIdentityComposer();
  fireEvent.click(screen.getByTitle("Edit"));
  fireEvent.change(chat.input, { target: { value: "A amended project draft" } });
  await act(async () => projectSecuritySession("synthetic-security-b"));
  await act(async () => chat.pending.resolve(Response.json(PROJECT_SECURITY_OTHER)));
  await waitFor(() => projectSecurityControls("project-security-b", true));
  const next = screen.getByRole<HTMLTextAreaElement>("textbox");
  expect(screen.queryByText("Editing message")).toBeNull();
  expect(next.value).toBe("");
  fireEvent.change(next, { target: { value: "B new project draft" } });
  await act(async () => fireEvent.keyDown(next, { key: "Enter" }));
  expect(chat.writes).toEqual([{ method: "POST", path: "/api/v1/chat/project/demo-project/human-messages",
    authorization: "Bearer synthetic-security-b", content: "B new project draft" }]);
});

it.each(["draft", "edit"])("SEC.CHAT project composer preserves %s while same-account profile reconfirms", async mode => {
  const chat = await openProjectIdentityComposer();
  if (mode === "edit") fireEvent.click(screen.getByTitle("Edit"));
  fireEvent.change(chat.input, { target: { value: "Unsaved project revision" } });
  await act(async () => projectSecuritySession("synthetic-refreshed-security-a"));
  expect(screen.queryByText("Editing message")).toBeNull();
  const visible = screen.queryByRole<HTMLTextAreaElement>("textbox");
  expect(visible === null || visible.disabled).toBe(true);
  await act(async () => fireEvent.keyDown(chat.input, { key: "Enter" }));
  expect(chat.writes).toEqual([]);
  await act(async () => chat.pending.resolve(Response.json(PROJECT_SECURITY_OWNER)));
  const restored = await screen.findByRole<HTMLTextAreaElement>("textbox");
  expect(restored.value).toBe("Unsaved project revision");
  await act(async () => fireEvent.keyDown(restored, { key: "Enter" }));
  expect(chat.writes).toEqual([{ method: mode === "edit" ? "PATCH" : "POST",
    path: mode === "edit" ? "/api/v1/chat/project/demo-project/human-messages/project-security-a" : "/api/v1/chat/project/demo-project/human-messages",
    authorization: "Bearer synthetic-refreshed-security-a", content: "Unsaved project revision" }]);
});

const PROFILE_COPY = {
  en: { checking: "Checking your account…", error: "Could not verify your account." },
  ru: { checking: "Проверяем аккаунт…", error: "Не удалось проверить аккаунт." },
};

async function openProfileState(locale: "en" | "ru" = "en") {
  const requests = { current: pendingSecurityProfile() };
  projectSecuritySession("synthetic-profile-a");
  const profiles = vi.fn(() => requests.current.promise.then(response => response.clone()));
  stubProjectSecurity(profiles);
  const view = render(<LocaleProvider initialLocale={locale}><ProjectPage /></LocaleProvider>);
  await act(async () => {});
  await act(async () => fireEvent.click(screen.getByRole("button", { name: /discussion|обсуждение/i })));
  screen.getByText("project-security-a");
  return { requests, profiles, view };
}

it.each(["en", "ru"] as const)("CH.4b project distinguishes checking from signed-out in %s", async locale => {
  await openProfileState(locale);
  expect(screen.getByText(PROFILE_COPY[locale].checking).closest('[role="status"]')).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  projectSecurityControls("project-security-a", false, locale);
  await act(async () => projectSecuritySession(null));
  expect(screen.queryByText(PROFILE_COPY[locale].checking)).toBeNull();
  expect(screen.getByText(locale === "en" ? "Want to join the discussion?" : "Хотите участвовать в обсуждении?")).toBeTruthy();
});

it.each([
  { locale: "en", failure: "http" }, { locale: "ru", failure: "http" },
  { locale: "en", failure: "network" }, { locale: "ru", failure: "network" },
  { locale: "en", failure: "json" }, { locale: "ru", failure: "json" },
  { locale: "en", failure: "schema" }, { locale: "ru", failure: "schema" },
] as const)("CH.4b project retries $failure profile verification truthfully in $locale", async ({ locale, failure }) => {
  const chat = await openProfileState(locale);
  await act(async () => {
    if (failure === "network") chat.requests.current.reject(new TypeError("Synthetic profile network failure"));
    else if (failure === "json") chat.requests.current.resolve(new Response("{invalid"));
    else if (failure === "schema") chat.requests.current.resolve(Response.json({ ...PROJECT_SECURITY_OWNER, id: null }));
    else chat.requests.current.resolve(Response.json({ detail: "Synthetic unavailable profile" }, { status: 503 }));
  });
  const alert = screen.getByText(PROFILE_COPY[locale].error).closest('[role="alert"]');
  if (!(alert instanceof HTMLElement)) throw new Error("Profile failure alert missing");
  expect(screen.queryByRole("textbox")).toBeNull();
  projectSecurityControls("project-security-a", false, locale);
  const beforeRetry = chat.profiles.mock.calls.length;
  chat.requests.current = pendingSecurityProfile();
  fireEvent.click(within(alert).getByRole("button", { name: sharedMessages[locale].retry }));
  await act(async () => {});
  expect(chat.profiles).toHaveBeenCalledTimes(beforeRetry + 1);
  expect(screen.getByText(PROFILE_COPY[locale].checking).closest('[role="status"]')).toBeTruthy();
  expect(screen.queryByText(PROFILE_COPY[locale].error)).toBeNull();
  await act(async () => chat.requests.current.resolve(Response.json(PROJECT_SECURITY_OWNER)));
  expect(screen.getByRole("textbox")).toBeTruthy();
  projectSecurityControls("project-security-a", true, locale);
  expect(screen.queryByText(PROFILE_COPY[locale].checking)).toBeNull();
  expect(screen.queryByText(PROFILE_COPY[locale].error)).toBeNull();
});

it.each([
  { locale: "en", late: "success" }, { locale: "ru", late: "success" },
  { locale: "en", late: "failure" }, { locale: "ru", late: "failure" },
] as const)("CH.4b project times out then ignores stale $late during retry in $locale", async ({ locale, late }) => {
  vi.useFakeTimers();
  const chat = await openProfileState(locale), old = chat.requests.current;
  await act(async () => vi.advanceTimersByTimeAsync(9_999));
  expect(screen.getByText(PROFILE_COPY[locale].checking)).toBeTruthy();
  await act(async () => vi.advanceTimersByTimeAsync(1));
  const alert = screen.getByText(PROFILE_COPY[locale].error).closest('[role="alert"]');
  if (!(alert instanceof HTMLElement)) throw new Error("Profile timeout alert missing");
  const signals = vi.mocked(fetch).mock.calls.filter(([input]) => String(input).endsWith("/auth/me")).map(([, init]) => init?.signal);
  expect(signals.some(signal => signal?.aborted)).toBe(true);
  chat.requests.current = pendingSecurityProfile();
  fireEvent.click(within(alert).getByRole("button", { name: sharedMessages[locale].retry }));
  await act(async () => {
    if (late === "success") old.resolve(Response.json(PROJECT_SECURITY_OWNER));
    else old.reject(new TypeError("Synthetic delayed profile failure"));
  });
  expect(screen.getByText(PROFILE_COPY[locale].checking)).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  await act(async () => chat.requests.current.resolve(Response.json(PROJECT_SECURITY_OWNER)));
  projectSecurityControls("project-security-a", true, locale);
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  expect(screen.queryByText(PROFILE_COPY[locale].error)).toBeNull();
  expect(screen.getByRole("textbox")).toBeTruthy();
});

it.each(["success", "failure", "timeout"])("CH.4b project ignores old %s after account replacement", async outcome => {
  vi.useFakeTimers();
  const chat = await openProfileState(), old = chat.requests.current;
  chat.requests.current = pendingSecurityProfile();
  await act(async () => projectSecuritySession("synthetic-profile-b"));
  await act(async () => chat.requests.current.resolve(Response.json(PROJECT_SECURITY_OTHER)));
  await act(async () => {
    if (outcome === "success") old.resolve(Response.json(PROJECT_SECURITY_OWNER));
    else if (outcome === "failure") old.reject(new TypeError("Synthetic previous account failure"));
    else await vi.advanceTimersByTimeAsync(10_000);
  });
  projectSecurityControls("project-security-a", false); projectSecurityControls("project-security-b", true);
  expect(screen.queryByText(PROFILE_COPY.en.error)).toBeNull();
  expect(screen.getByRole("textbox")).toBeTruthy();
});

it("CH.4b project aborts verification and its deadline after unmount", async () => {
  vi.useFakeTimers();
  const chat = await openProfileState();
  const signals = vi.mocked(fetch).mock.calls.filter(([input]) => String(input).endsWith("/auth/me")).map(([, init]) => init?.signal);
  expect(signals.length).toBeGreaterThan(0);
  chat.view.unmount();
  expect(signals.every(signal => signal?.aborted)).toBe(true);
  const requests = vi.mocked(fetch).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(20_000); chat.requests.current.resolve(Response.json(PROJECT_SECURITY_OWNER)); });
  expect(fetch).toHaveBeenCalledTimes(requests);
});

it.each(["draft", "edit"])("CH.4b project retains %s through same-account error and retry", async mode => {
  const chat = await openProfileState();
  await act(async () => chat.requests.current.resolve(Response.json(PROJECT_SECURITY_OWNER)));
  const input = screen.getByRole<HTMLTextAreaElement>("textbox");
  if (mode === "edit") fireEvent.click(screen.getAllByTitle("Edit")[0]);
  fireEvent.change(input, { target: { value: "A revision retained through profile failure" } });
  chat.requests.current = pendingSecurityProfile();
  await act(async () => projectSecuritySession("synthetic-profile-a-refreshed"));
  await act(async () => chat.requests.current.resolve(Response.json({}, { status: 503 })));
  const alert = screen.getByText(PROFILE_COPY.en.error).closest('[role="alert"]');
  if (!(alert instanceof HTMLElement)) throw new Error("Profile retry alert missing");
  chat.requests.current = pendingSecurityProfile();
  fireEvent.click(within(alert).getByRole("button", { name: sharedMessages.en.retry }));
  await act(async () => chat.requests.current.resolve(Response.json(PROJECT_SECURITY_OWNER)));
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe("A revision retained through profile failure");
  expect(screen.queryByText("Editing message") !== null).toBe(mode === "edit");
});

const PROJECT_DRAFT_SECOND = {
  ...PROJECT_SECURITY_ROWS[0], id: "project-owned-second", content: "Second owned message",
  sender_name: "Second owned sender", sender_user_id: PROJECT_SECURITY_OWNER.id,
};

async function openPendingProjectDraft() {
  const mutation = pendingSecurityProfile(), profile = pendingSecurityProfile();
  const writes: { method: string; path: string; authorization: string | null; body: unknown }[] = [];
  projectSecuritySession("synthetic-security-a");
  stubProjectSecurity(authorization => authorization === "Bearer synthetic-security-a"
    ? Promise.resolve(Response.json(PROJECT_SECURITY_OWNER)) : profile.promise.then(response => response.clone()));
  const read = fetch;
  vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => {
    if (init?.method === "POST" || init?.method === "PATCH") {
      writes.push({ method: init.method, path: new URL(input).pathname,
        authorization: new Headers(init.headers).get("Authorization"), body: JSON.parse(String(init.body)) });
      return mutation.promise.then(response => response.clone());
    }
    if (input.includes("/chat/project/demo-project/messages")) {
      return Promise.resolve(Response.json([...PROJECT_SECURITY_ROWS, PROJECT_DRAFT_SECOND].reverse()));
    }
    return read(input, init);
  }));
  await openProjectSecurity();
  await waitFor(() => expect(screen.getAllByTitle("Edit")).toHaveLength(2));
  return { mutation, profile, writes };
}

function projectDraftAction(content: string, title: "Edit" | "Reply") {
  const row = screen.getByText(content).closest(".group");
  if (!(row instanceof HTMLElement)) throw new Error("Project draft message row missing");
  fireEvent.click(within(row).getByTitle(title));
}

function changeProjectDraft(content: string) {
  const input = screen.getByRole<HTMLTextAreaElement>("textbox");
  fireEvent.change(input, { target: { value: content } });
  return input;
}

async function submitProjectDraft() {
  await act(async () => fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" }));
}

function expectProjectWrite(writes: { method: string; path: string; authorization: string | null; body: unknown }[], method: "POST" | "PATCH", reply = false) {
  expect(writes).toEqual([{
    method, path: "/api/v1/chat/project/demo-project/human-messages" + (method === "PATCH" ? "/project-security-a" : ""),
    authorization: "Bearer synthetic-security-a",
    body: method === "PATCH" ? { content: "Submitted project text" }
      : { name: PROJECT_SECURITY_OWNER.name, content: "Submitted project text", message_type: "text",
        ...(reply ? { reply_to_id: "project-security-a" } : {}) },
  }]);
}

it.each([false, true])("CH.2p P1 POST success clears only unchanged draft; ABA=%s", async revised => {
  const chat = await openPendingProjectDraft();
  changeProjectDraft("Submitted project text");
  await submitProjectDraft();
  if (revised) {
    changeProjectDraft("Intermediate project text");
    changeProjectDraft("Submitted project text");
  }
  expectProjectWrite(chat.writes, "POST");
  await act(async () => chat.mutation.resolve(Response.json({ status: "ok" })));
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe(revised ? "Submitted project text" : "");
  expect(screen.queryByText("Editing message")).toBeNull();
});

it.each([false, true])("CH.2p P2 PATCH updates submitted row and clears only unchanged editor; ABA=%s", async revised => {
  const chat = await openPendingProjectDraft();
  projectDraftAction("project-security-a", "Edit");
  changeProjectDraft("Submitted project text");
  await submitProjectDraft();
  if (revised) {
    changeProjectDraft("Intermediate project text");
    changeProjectDraft("Submitted project text");
  }
  expectProjectWrite(chat.writes, "PATCH");
  await act(async () => chat.mutation.resolve(Response.json({ status: "ok" })));
  expect(screen.getByText("Submitted project text", { selector: ".group p" }).closest(".group")).toBeTruthy();
  expect(screen.queryByText("project-security-a")).toBeNull();
  expect(screen.getByText("Second owned message")).toBeTruthy();
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe(revised ? "Submitted project text" : "");
  expect(screen.queryByText("Editing message") !== null).toBe(revised);
});

it("CH.2p P3 late PATCH preserves normal draft after Escape", async () => {
  const chat = await openPendingProjectDraft();
  projectDraftAction("project-security-a", "Edit");
  changeProjectDraft("Submitted project text");
  await submitProjectDraft();
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
  changeProjectDraft("A newer normal project draft");
  await act(async () => chat.mutation.resolve(Response.json({ status: "ok" })));
  expectProjectWrite(chat.writes, "PATCH");
  expect(screen.getByText("Submitted project text")).toBeTruthy();
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe("A newer normal project draft");
  expect(screen.queryByText("Editing message")).toBeNull();
});

it("CH.2p P4 late PATCH preserves normal draft after Cancel button", async () => {
  const chat = await openPendingProjectDraft();
  projectDraftAction("project-security-a", "Edit");
  changeProjectDraft("Submitted project text");
  await submitProjectDraft();
  const form = screen.getByRole("textbox").closest("form");
  if (!form) throw new Error("Project draft form missing");
  const cancel = form.querySelector('button[type="button"]');
  if (!(cancel instanceof HTMLButtonElement)) throw new Error("Project cancel control missing");
  fireEvent.click(cancel);
  changeProjectDraft("A newer cancelled project draft");
  await act(async () => chat.mutation.resolve(Response.json({ status: "ok" })));
  expectProjectWrite(chat.writes, "PATCH");
  expect(screen.getByText("Submitted project text")).toBeTruthy();
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe("A newer cancelled project draft");
  expect(screen.queryByText("Editing message")).toBeNull();
});

it("CH.2p P5 late PATCH A preserves a newer editor B and its row", async () => {
  const chat = await openPendingProjectDraft();
  projectDraftAction("project-security-a", "Edit");
  changeProjectDraft("Submitted project text");
  await submitProjectDraft();
  projectDraftAction("Second owned message", "Edit");
  changeProjectDraft("B new editor draft");
  await act(async () => chat.mutation.resolve(Response.json({ status: "ok" })));
  expectProjectWrite(chat.writes, "PATCH");
  expect(screen.getByText("Submitted project text")).toBeTruthy();
  expect(screen.getByText("Second owned message")).toBeTruthy();
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe("B new editor draft");
  expect(screen.getByText("Editing message")).toBeTruthy();
  await submitProjectDraft();
  expect(chat.writes[1]).toEqual({ method: "PATCH",
    path: "/api/v1/chat/project/demo-project/human-messages/project-owned-second",
    authorization: "Bearer synthetic-security-a", body: { content: "B new editor draft" } });
});

it("CH.2p P6 late PATCH preserves a fresh same-row same-text editor", async () => {
  const chat = await openPendingProjectDraft();
  projectDraftAction("project-security-a", "Edit");
  changeProjectDraft("Submitted project text");
  await submitProjectDraft();
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
  projectDraftAction("project-security-a", "Edit");
  changeProjectDraft("Submitted project text");
  await act(async () => chat.mutation.resolve(Response.json({ status: "ok" })));
  expectProjectWrite(chat.writes, "PATCH");
  expect(screen.getByText("Submitted project text", { selector: ".group p" })).toBeTruthy();
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe("Submitted project text");
  expect(screen.getByText("Editing message")).toBeTruthy();
});

it("CH.2p P7 late reply POST keeps submitted target A and current reply B", async () => {
  const chat = await openPendingProjectDraft();
  projectDraftAction("project-security-a", "Reply");
  changeProjectDraft("Submitted project text");
  await submitProjectDraft();
  projectDraftAction("Second owned message", "Reply");
  changeProjectDraft("B next reply draft");
  expectProjectWrite(chat.writes, "POST", true);
  await act(async () => chat.mutation.resolve(Response.json({ status: "ok" })));
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe("B next reply draft");
  expect(screen.getByText("Replying to Second owned sender")).toBeTruthy();
  expect(screen.getByRole("textbox").getAttribute("placeholder")).toBe("Reply to Second owned sender...");
});

it("CH.2p P8 same-owner reconfirmation cannot duplicate an owned pending POST", async () => {
  const chat = await openPendingProjectDraft();
  changeProjectDraft("Submitted project text");
  await submitProjectDraft();
  changeProjectDraft("Current generation draft");
  await act(async () => projectSecuritySession("synthetic-reconfirmed-a"));
  expect(screen.queryByRole("textbox")).toBeNull();
  await act(async () => chat.profile.resolve(Response.json(PROJECT_SECURITY_OWNER)));
  const input = screen.getByRole<HTMLTextAreaElement>("textbox");
  expect(input.value).toBe("Current generation draft");
  await act(async () => fireEvent.keyDown(input, { key: "Enter" }));
  expectProjectWrite(chat.writes, "POST");
  await act(async () => chat.mutation.resolve(Response.json({ status: "ok" })));
  expect(input.value).toBe("Current generation draft");
  await act(async () => fireEvent.keyDown(input, { key: "Enter" }));
  expect(chat.writes).toHaveLength(2);
  expect(chat.writes[1]).toEqual({ method: "POST", path: "/api/v1/chat/project/demo-project/human-messages",
    authorization: "Bearer synthetic-reconfirmed-a",
    body: { name: PROJECT_SECURITY_OWNER.name, content: "Current generation draft", message_type: "text" } });
});

it.each(["http", "network"])("CH.2p P9 failed POST %s retains newer draft and releases retry", async failure => {
  const chat = await openPendingProjectDraft();
  changeProjectDraft("Submitted project text");
  await submitProjectDraft();
  changeProjectDraft("A newer retry draft");
  await act(async () => {
    if (failure === "http") chat.mutation.resolve(Response.json({ detail: "Synthetic unavailable" }, { status: 503 }));
    else chat.mutation.reject(new TypeError("Synthetic write network failure"));
  });
  expectProjectWrite(chat.writes, "POST");
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe("A newer retry draft");
  const read = fetch;
  const retry = vi.fn((input: string, init?: RequestInit) => {
    expect(new URL(input).pathname).toBe("/api/v1/chat/project/demo-project/human-messages");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer synthetic-security-a");
    expect(JSON.parse(String(init?.body))).toEqual({ name: PROJECT_SECURITY_OWNER.name,
      content: "A newer retry draft", message_type: "text" });
    return Promise.resolve(Response.json({ status: "ok" }));
  });
  vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => init?.method === "POST" ? retry(input, init) : read(input, init)));
  await submitProjectDraft();
  expect(retry).toHaveBeenCalledTimes(1);
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe("");
});

it("CH.2p P10 old A completion cannot unlock or clear B pending POST", async () => {
  const chat = await openPendingProjectDraft(), bPending = pendingSecurityProfile();
  const bWrites: typeof chat.writes = [];
  changeProjectDraft("Submitted project text");
  await submitProjectDraft();
  const read = fetch;
  vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => {
    const authorization = new Headers(init?.headers).get("Authorization");
    if (input.endsWith("/auth/me") && authorization === "Bearer synthetic-owner-b") return Promise.resolve(Response.json(PROJECT_SECURITY_OTHER));
    if (init?.method === "POST" && authorization === "Bearer synthetic-owner-b") {
      bWrites.push({ method: init.method, path: new URL(input).pathname, authorization, body: JSON.parse(String(init.body)) });
      return bPending.promise.then(response => response.clone());
    }
    return read(input, init);
  }));
  await act(async () => projectSecuritySession("synthetic-owner-b"));
  await waitFor(() => projectSecurityControls("project-security-b", true));
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe("");
  changeProjectDraft("B current submitted draft");
  await submitProjectDraft();
  expect(bWrites).toEqual([{ method: "POST", path: "/api/v1/chat/project/demo-project/human-messages",
    authorization: "Bearer synthetic-owner-b", body: { name: PROJECT_SECURITY_OTHER.name,
      content: "B current submitted draft", message_type: "text" } }]);
  await act(async () => chat.mutation.resolve(Response.json({ status: "ok" })));
  expectProjectWrite(chat.writes, "POST");
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe("B current submitted draft");
  expect(screen.getByText("project-security-a")).toBeTruthy();
  expect(screen.getByText("project-security-b")).toBeTruthy();
  await submitProjectDraft();
  expect(bWrites).toHaveLength(1);
  await act(async () => bPending.resolve(Response.json({ status: "ok" })));
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe("");
});

it("CH.2p P10 unmounted POST completion cannot reload project history", async () => {
  const chat = await openPendingProjectDraft();
  changeProjectDraft("Submitted project text");
  await submitProjectDraft();
  expectProjectWrite(chat.writes, "POST");
  const requests = vi.mocked(fetch);
  cleanup();
  const historyReads = () => requests.mock.calls.filter(([input, init]) => String(input).includes("/chat/project/demo-project/messages") && (!init?.method || init.method === "GET")).length;
  const before = historyReads(), allBefore = requests.mock.calls.length;
  expect(before).toBeGreaterThan(0);
  await act(async () => chat.mutation.resolve(Response.json({ status: "ok" })));
  expect(historyReads()).toBe(before);
  expect(requests).toHaveBeenCalledTimes(allBefore);
});
