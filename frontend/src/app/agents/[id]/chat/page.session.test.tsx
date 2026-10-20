// @vitest-environment jsdom
import { act, fireEvent, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { A, B, TOKEN_A, TOKEN_B, alertFor, assertWrite, boundary, copy, disclosure, fail, finish, input, message, noRawFailure, openChat, reads, retry, send, session, typeDraft, writes } from './directChatFixtures';

it.each(['en', 'ru'] as const)('DM.4 profile checking in %s is distinct from sign-out', async locale => {
  session(TOKEN_A); const http = boundary();
  const header = http.hold(`profile:Bearer ${TOKEN_A}`), profile = http.hold(`profile:Bearer ${TOKEN_A}`);
  await openChat(http, locale);
  expect(screen.getByText(copy(locale, 'Checking your account…')).closest('[role="status"]')).toBeTruthy();
  expect(screen.queryByRole('textbox')).toBeNull(); expect(screen.getByText(disclosure[locale])).toBeTruthy();
  await finish(header, A); await finish(profile, A);
  expect(input()).toBeTruthy(); expect(screen.queryByText(copy(locale, 'Checking your account…'))).toBeNull(); noRawFailure(http);
});

it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }] as const)(
  'DM.4 profile $mode failure in $locale retries only current verification', async ({ locale, mode }) => {
    session(TOKEN_A); const http = boundary();
    const header = http.hold(`profile:Bearer ${TOKEN_A}`), profile = http.hold(`profile:Bearer ${TOKEN_A}`);
    await openChat(http, locale); await fail(header, mode); await fail(profile, mode);
    const error = copy(locale, 'Could not verify your account.'); alertFor(error);
    expect(screen.queryByRole('textbox')).toBeNull();
    const before = http.calls.filter(call => call.path.endsWith('/auth/me')).length;
    const next = http.hold(`profile:Bearer ${TOKEN_A}`); retry(error, locale);
    expect(http.calls.filter(call => call.path.endsWith('/auth/me'))).toHaveLength(before + 1);
    await finish(next, A); expect(input()).toBeTruthy(); noRawFailure(http);
  });

it.each(['logout', 'replacement'] as const)('DM.4 stale profile cannot restore old composer after %s', async transition => {
  session(TOKEN_A); const http = boundary();
  const header = http.hold(`profile:Bearer ${TOKEN_A}`), profile = http.hold(`profile:Bearer ${TOKEN_A}`);
  await openChat(http);
  await act(async () => session(transition === 'logout' ? null : TOKEN_B));
  await finish(header, A); await finish(profile, A);
  if (transition === 'logout') {
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.getByRole('link', { name: copy('en', 'Sign in to send messages') })).toBeTruthy();
  } else {
    typeDraft('B verified body'); send(); await act(async () => {});
    assertWrite(http, 'B verified body', B, TOKEN_B);
  }
  noRawFailure(http);
});

it('DM.4 verification deadline releases Retry and ignores stale success', async () => {
  vi.useFakeTimers(); session(TOKEN_A); const http = boundary();
  const first = http.hold(`profile:Bearer ${TOKEN_A}`), second = http.hold(`profile:Bearer ${TOKEN_A}`);
  await openChat(http);
  await act(async () => vi.advanceTimersByTimeAsync(9999));
  expect(screen.getByText(copy('en', 'Checking your account…'))).toBeTruthy();
  await act(async () => vi.advanceTimersByTimeAsync(1));
  const error = copy('en', 'Could not verify your account.'); alertFor(error);
  const next = http.hold(`profile:Bearer ${TOKEN_A}`); retry(error);
  await finish(first, B); await finish(second, B); expect(screen.queryByRole('textbox')).toBeNull();
  await finish(next, A); typeDraft('Verified A'); send(); await act(async () => {});
  assertWrite(http, 'Verified A'); noRawFailure(http);
});

it.each(['newer draft', 'ABA'] as const)('DM.5 late POST success retains %s revision', async scenario => {
  session(TOKEN_A); const http = boundary(); await openChat(http);
  const post = http.hold('post:synthetic-a'); typeDraft('Original'); send();
  assertWrite(http, 'Original'); typeDraft('Intermediate');
  const current = scenario === 'ABA' ? 'Original' : 'Newer unsent'; typeDraft(current);
  http.rows.set('synthetic-a', [message('accepted', 'Accepted original')]);
  await finish(post, { status: 'ok' });
  expect(input().value).toBe(current); expect(screen.getByText('Accepted original')).toBeTruthy();
  expect(writes(http)).toHaveLength(1); noRawFailure(http);
});

it('DM.5 unchanged accepted POST clears its form and supersedes an existing poll', async () => {
  vi.useFakeTimers(); session(TOKEN_A); const http = boundary(); await openChat(http);
  const oldPoll = http.hold('poll:synthetic-a');
  await act(async () => vi.advanceTimersByTimeAsync(5000));
  expect(reads(http, 'poll')).toHaveLength(1);
  const post = http.hold('post:synthetic-a'); typeDraft('  Submitted body  '); send(); assertWrite(http, 'Submitted body');
  const freshPoll = http.hold('poll:synthetic-a');
  await finish(post, { status: 'ok' });
  expect(input().value).toBe(''); expect(reads(http, 'poll')).toHaveLength(2);
  await finish(freshPoll, [message('accepted', 'Accepted body')]);
  expect(screen.getAllByText('Accepted body')).toHaveLength(1);
  await finish(oldPoll, []);
  expect(screen.getAllByText('Accepted body')).toHaveLength(1);
  expect(input().value).toBe(''); expect(writes(http)).toHaveLength(1); noRawFailure(http);
});

it.each(['http', 'network'] as const)('DM.6 POST %s failure retains current draft and permits current retry', async mode => {
  session(TOKEN_A); const http = boundary(); await openChat(http);
  const post = http.hold('post:synthetic-a'); typeDraft('Submitted'); send(); typeDraft('New retry draft');
  await fail(post, mode); alertFor(copy('en', 'Failed to send'));
  expect(input().value).toBe('New retry draft'); noRawFailure(http);
  const next = http.hold('post:synthetic-a'); send();
  expect(writes(http)).toHaveLength(2); assertWrite(http, 'New retry draft');
  http.rows.set('synthetic-a', [message('retried', 'Retried body')]); await finish(next, { status: 'ok' });
  expect(input().value).toBe(''); expect(screen.getByText('Retried body')).toBeTruthy(); noRawFailure(http);
});

it('DM.6 same-turn duplicate submits remain single-flight and later writes are released', async () => {
  session(TOKEN_A); const http = boundary(); await openChat(http);
  const post = http.hold('post:synthetic-a'); typeDraft('First');
  const form = input().closest('form'); if (!form) throw new Error('Missing direct chat form');
  act(() => { fireEvent.submit(form); fireEvent.submit(form); });
  expect(writes(http)).toHaveLength(1); await finish(post, { status: 'ok' });
  const next = http.hold('post:synthetic-a'); typeDraft('Second'); send();
  expect(writes(http)).toHaveLength(2); assertWrite(http, 'Second');
  await finish(next, { status: 'ok' }); expect(input().value).toBe(''); noRawFailure(http);
});

it('DM.7 old principal success cannot clear or unlock a new principal write', async () => {
  session(TOKEN_A); const http = boundary(); await openChat(http);
  const old = http.hold('post:synthetic-a'); typeDraft('A submitted'); send();
  await act(async () => session(TOKEN_B)); expect(input().value).toBe('');
  const current = http.hold('post:synthetic-a'); typeDraft('B submitted'); send();
  expect(writes(http)).toHaveLength(2); assertWrite(http, 'B submitted', B, TOKEN_B);
  const polls = reads(http, 'poll').length; await finish(old, { status: 'ok' });
  expect(input().value).toBe('B submitted'); expect(reads(http, 'poll')).toHaveLength(polls);
  send(); expect(writes(http)).toHaveLength(2);
  await finish(current, { status: 'ok' }); expect(input().value).toBe(''); noRawFailure(http);
});

it('DM.7 old route POST cannot clear current draft or refresh old history', async () => {
  session(TOKEN_A); const http = boundary(); const view = await openChat(http);
  const old = http.hold('post:synthetic-a'); typeDraft('A body'); send();
  await view.navigate('agent-b'); typeDraft('B new draft'); const before = reads(http, 'poll').length;
  await finish(old, { status: 'ok' });
  expect(input().value).toBe('B new draft'); expect(reads(http, 'poll')).toHaveLength(before);
  const current = http.hold('post:synthetic-b'); send();
  expect(writes(http, 'synthetic-b')).toHaveLength(1);
  expect(JSON.parse(writes(http, 'synthetic-b')[0].body ?? '{}')).toEqual({ name: A.name, content: 'B new draft' });
  await finish(current, { status: 'ok' }); expect(input().value).toBe(''); noRawFailure(http);
});

it('DM.7 unmounted POST completion cannot launch a refresh', async () => {
  session(TOKEN_A); const http = boundary(); const view = await openChat(http);
  const post = http.hold('post:synthetic-a'); typeDraft('Unmount body'); send();
  view.unmount(); const requests = http.calls.length; await finish(post, { status: 'ok' });
  expect(http.calls).toHaveLength(requests); noRawFailure(http);
});
