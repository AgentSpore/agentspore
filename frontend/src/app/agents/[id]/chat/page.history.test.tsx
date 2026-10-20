// @vitest-environment jsdom
import { act, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { A, AGENTS, ROWS, TOKEN_A, alertFor, boundary, copy, disclosure, fail, finish, input, message, noRawFailure, openChat, pollCopy, reads, renderChat, retry, scrollTopTwice, session, typeDraft } from './directChatFixtures';

it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }] as const)(
  'DM.1 initial $mode failure in $locale remains distinct from empty and retries', async ({ locale, mode }) => {
    session(TOKEN_A); const http = boundary(), first = http.hold('initial:synthetic-a');
    await openChat(http, locale);
    expect(screen.getByText(copy(locale, 'Loading messages...')).closest('[role="status"]')).toBeTruthy();
    expect(screen.queryByText(copy(locale, 'No messages yet'))).toBeNull();
    typeDraft('Retained read draft');
    await fail(first, mode);
    alertFor(copy(locale, 'Could not load messages.'));
    expect(screen.queryByText(copy(locale, 'No messages yet'))).toBeNull();
    const next = http.hold('initial:synthetic-a');
    retry(copy(locale, 'Could not load messages.'), locale);
    expect(reads(http)).toHaveLength(2);
    await finish(next, [message('same-name', 'Same-name visitor'), { ...message('foreign', 'Foreign visitor'), from_name: A.name }]);
    expect(screen.getByText('Same-name visitor')).toBeTruthy();
    expect(screen.getByText('Foreign visitor')).toBeTruthy();
    expect(screen.getAllByText(copy(locale, 'human')).length).toBeGreaterThan(0);
    expect(screen.queryByText(copy(locale, 'you'))).toBeNull();
    expect(screen.getByText(disclosure[locale])).toBeTruthy();
    expect(input().value).toBe('Retained read draft'); noRawFailure(http);
  });

it.each(['en', 'ru'] as const)('DM.1 anonymous public history confirms empty only after 200 [] in %s', async locale => {
  const http = boundary(), initial = http.hold('initial:synthetic-a');
  await openChat(http, locale);
  expect(screen.queryByText(copy(locale, 'No messages yet'))).toBeNull();
  await finish(initial, []);
  expect(screen.getByText(copy(locale, 'No messages yet'))).toBeTruthy();
  expect(screen.getByText(disclosure[locale])).toBeTruthy();
  expect(screen.getByRole('link', { name: copy(locale, 'Sign in to send messages') })).toBeTruthy();
  expect(screen.queryByRole('textbox')).toBeNull();
  expect(reads(http)[0].headers.has('Authorization')).toBe(false);
  noRawFailure(http);
});

it.each(['http', 'network'] as const)('DM.2 older %s failure preserves rows and retries one cursor', async mode => {
  session(TOKEN_A); const http = boundary(); http.rows.set('synthetic-a', ROWS);
  await openChat(http); typeDraft('Older retained draft');
  const older = http.hold('older:synthetic-a'); scrollTopTwice();
  expect(reads(http, 'older')).toHaveLength(1);
  expect(reads(http, 'older')[0].query).toBe('?limit=50&before=row-49');
  await fail(older, mode);
  for (const row of ROWS) expect(screen.getByText(row.content)).toBeTruthy();
  expect(input().value).toBe('Older retained draft');
  scrollTopTwice(); expect(reads(http, 'older')).toHaveLength(1);
  const next = http.hold('older:synthetic-a'); retry(copy('en', 'Could not load older messages.'));
  expect(reads(http, 'older')).toHaveLength(2);
  expect(reads(http, 'older')[1].query).toBe('?limit=50&before=row-49');
  await finish(next, [message('older-only')]);
  expect(screen.getAllByText('Message older-only')).toHaveLength(1);
  scrollTopTwice(); expect(reads(http, 'older')).toHaveLength(2);
  noRawFailure(http);
});

it('DM.3 concurrent poll survives a later initial snapshot without duplicate rows', async () => {
  vi.useFakeTimers(); const http = boundary(), initial = http.hold('initial:synthetic-a');
  await openChat(http); const poll = http.hold('poll:synthetic-a');
  await act(async () => vi.advanceTimersByTimeAsync(5000));
  expect(reads(http, 'poll')).toHaveLength(1);
  await finish(poll, [message('new'), message('shared')]);
  await finish(initial, [message('shared'), message('old')]);
  for (const id of ['new', 'shared', 'old']) expect(screen.getAllByText(`Message ${id}`)).toHaveLength(1);
  noRawFailure(http);
});

it('DM.3 overlapping older page and poll retain unique history and current draft', async () => {
  vi.useFakeTimers(); session(TOKEN_A); const http = boundary(); http.rows.set('synthetic-a', ROWS);
  await openChat(http); typeDraft('Concurrent draft');
  const older = http.hold('older:synthetic-a'); scrollTopTwice();
  const poll = http.hold('poll:synthetic-a');
  await act(async () => vi.advanceTimersByTimeAsync(5000));
  await finish(poll, [message('new'), ROWS[0]]);
  await finish(older, [ROWS[49], message('old')]);
  expect(screen.getAllByText(ROWS[49].content)).toHaveLength(1);
  expect(screen.getByText('Message new')).toBeTruthy(); expect(screen.getByText('Message old')).toBeTruthy();
  expect(input().value).toBe('Concurrent draft'); noRawFailure(http);
});

it('DM.3 failed polling retains history and draft with explicit retry and future automatic recovery', async () => {
  vi.useFakeTimers(); session(TOKEN_A); const http = boundary(); http.rows.set('synthetic-a', [message('original')]);
  await openChat(http); typeDraft('Poll retained draft'); const poll = http.hold('poll:synthetic-a');
  await act(async () => vi.advanceTimersByTimeAsync(5000)); await fail(poll, 'network');
  alertFor(pollCopy.en); expect(screen.getByText('Message original')).toBeTruthy();
  expect(input().value).toBe('Poll retained draft');
  const next = http.hold('poll:synthetic-a'); retry(pollCopy.en);
  expect(reads(http, 'poll')).toHaveLength(2);
  await finish(next, [message('recovered'), message('original')]);
  expect(screen.getByText('Message recovered')).toBeTruthy();
  http.rows.set('synthetic-a', [message('automatic'), message('recovered')]);
  await act(async () => vi.advanceTimersByTimeAsync(5000));
  expect(screen.getByText('Message automatic')).toBeTruthy();
  expect(reads(http, 'poll')).toHaveLength(3); noRawFailure(http);
});

it.each(['success', 'failure'] as const)('DM.7 old route initial %s cannot replace current route history', async outcome => {
  vi.useFakeTimers(); const http = boundary(), old = http.hold('initial:synthetic-a');
  const view = await openChat(http); http.rows.set('synthetic-b', [message('current-b')]);
  await view.navigate('agent-b');
  expect(screen.getByText('Synthetic Agent B')).toBeTruthy();
  if (outcome === 'success') await finish(old, [message('stale-a')]);
  else await fail(old, 'network');
  expect(screen.getByText('Message current-b')).toBeTruthy(); expect(screen.queryByText('Message stale-a')).toBeNull();
  const aPolls = reads(http, 'poll').length;
  await act(async () => vi.advanceTimersByTimeAsync(5000));
  expect(reads(http, 'poll')).toHaveLength(aPolls); expect(reads(http, 'poll', 'synthetic-b')).toHaveLength(1);
  expect(screen.queryByText(copy('en', 'Could not load messages.'))).toBeNull(); noRawFailure(http);
});

it('DM.7 unmount cancels future polling while old read completion stays inert', async () => {
  vi.useFakeTimers(); const http = boundary(), initial = http.hold('initial:synthetic-a');
  const view = await openChat(http); view.unmount(); const count = http.calls.length;
  await finish(initial, [message('after-unmount')]);
  await act(async () => vi.advanceTimersByTimeAsync(10000));
  expect(http.calls).toHaveLength(count); expect(screen.queryByText('Message after-unmount')).toBeNull(); noRawFailure(http);
});

it('DM.0 transient agent failure offers generic Retry instead of a false 404', async () => {
  const http = boundary(), agent = http.hold('agent:agent-a');
  const view = renderChat();
  await act(async () => {}); await fail(agent, 'http');
  alertFor('Could not load agent.'); expect(screen.queryByText('Agent not found')).toBeNull();
  const next = http.hold('agent:agent-a'); retry('Could not load agent.');
  await finish(next, AGENTS['agent-a']);
  expect(screen.getByText('Synthetic Agent A')).toBeTruthy(); noRawFailure(http); view.unmount();
});


it('DM.3 same-owner session refresh preserves older-first chronology and pagination cursor', async () => {
  session(TOKEN_A); const http = boundary(); http.rows.set('synthetic-a', ROWS);
  await openChat(http);
  const older = http.hold('older:synthetic-a'); scrollTopTwice();
  await finish(older, [message('older-only')]);
  const refreshed = http.hold('initial:synthetic-a');
  await act(async () => session('synthetic-same-owner-replacement'));
  await finish(refreshed, ROWS);
  const ids = [...screen.getByRole('main').querySelectorAll('[id^="dm-"]')].map(element => element.id);
  expect(ids[0]).toBe('dm-older-only');
  expect(ids.at(-1)).toBe('dm-row-0');
  expect(screen.getAllByText('Message older-only')).toHaveLength(1);
  const next = http.hold('older:synthetic-a'); scrollTopTwice();
  expect(reads(http, 'older').at(-1)?.query).toBe('?limit=50&before=older-only');
  await finish(next, []); noRawFailure(http);
});

it('DM.3 disjoint delayed initial page stays before newer polled rows and keeps oldest cursor', async () => {
  vi.useFakeTimers(); const http = boundary(), initial = http.hold('initial:synthetic-a');
  const oldRows = ROWS.map((item, index) => ({ ...item, created_at: `2026-10-20T10:${String(49 - index).padStart(2, '0')}:00Z` }));
  const newRows = Array.from({ length: 10 }, (_, index) => ({ ...message(`new-${index}`), created_at: `2026-10-20T11:${String(9 - index).padStart(2, '0')}:00Z` }));
  await openChat(http); const poll = http.hold('poll:synthetic-a');
  await act(async () => vi.advanceTimersByTimeAsync(5000));
  await finish(poll, newRows); await finish(initial, oldRows);
  const ids = [...screen.getByRole('main').querySelectorAll('[id^="dm-"]')].map(element => element.id);
  expect(ids).toEqual([...oldRows].reverse().concat([...newRows].reverse()).map(item => `dm-${item.id}`));
  const older = http.hold('older:synthetic-a'); scrollTopTwice();
  expect(reads(http, 'older')).toHaveLength(1);
  expect(reads(http, 'older')[0].query).toBe('?limit=50&before=row-49');
  await finish(older, []); noRawFailure(http);
});
