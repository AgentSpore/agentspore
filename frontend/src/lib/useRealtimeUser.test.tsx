// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Suspense, startTransition, useLayoutEffect, useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RealtimeUserProvider, useRealtimeUser, type RealtimeEvent } from "./useRealtimeUser";

type Callback = (event: RealtimeEvent) => void;
const sockets: SyntheticSocket[] = [];

class SyntheticSocket {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();
  constructor(readonly url: string) { sockets.push(this); }
  receive(data: string) { this.onmessage?.({ data }); }
}

function socket() {
  const current = sockets[0];
  if (!current) throw new Error("Expected a provider WebSocket");
  return current;
}

function Consumer({ name, onEvent }: { name: string; onEvent?: Callback }) {
  const { last, connected } = useRealtimeUser(onEvent);
  return <output aria-label={name}>{JSON.stringify({ last, connected })}</output>;
}

function CommitEmitter({ event }: { event: string }) {
  useLayoutEffect(() => { socket().receive(event); }, [event]);
  return null;
}

beforeEach(() => {
  sockets.length = 0;
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", SyntheticSocket);
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => "synthetic-test-token") });
});

afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("shares one authenticated socket, snapshots events and removes unmounted subscribers", () => {
  const first = vi.fn();
  const second = vi.fn();
  const view = render(<RealtimeUserProvider>
    <Consumer key="first" name="first" onEvent={first} /><Consumer key="second" name="second" onEvent={second} />
  </RealtimeUserProvider>);
  expect(sockets).toHaveLength(1);
  expect(new URL(socket().url).searchParams.get("token")).toBe("synthetic-test-token");
  act(() => { socket().onopen?.(); });
  const event = { type: "notification", text: "One" };
  act(() => { socket().receive(JSON.stringify(event)); });
  expect(first).toHaveBeenCalledExactlyOnceWith(event);
  expect(second).toHaveBeenCalledExactlyOnceWith(event);
  for (const name of ["first", "second"]) {
    expect(JSON.parse(screen.getByLabelText(name).textContent ?? "")).toEqual({ last: event, connected: true });
  }
  view.rerender(<RealtimeUserProvider><Consumer key="second" name="second" onEvent={second} /></RealtimeUserProvider>);
  const next = { type: "notification", text: "Two" };
  act(() => { socket().receive(JSON.stringify(next)); });
  expect(first).toHaveBeenCalledTimes(1);
  expect(second).toHaveBeenCalledTimes(2);
  expect(second).toHaveBeenLastCalledWith(next);
  expect(sockets).toHaveLength(1);
  view.unmount();
  expect(socket().close).toHaveBeenCalledTimes(1);
  act(() => { socket().receive(JSON.stringify(next)); });
  expect(second).toHaveBeenCalledTimes(2);
});

it("publishes replacement callbacks before sibling layout effects without reconnecting", () => {
  const previous = vi.fn();
  const replacement = vi.fn();
  const view = render(<RealtimeUserProvider><Consumer name="subscriber" onEvent={previous} /></RealtimeUserProvider>);
  const event = { type: "notification", text: "During commit" };
  view.rerender(<RealtimeUserProvider>
    <Consumer name="subscriber" onEvent={replacement} /><CommitEmitter event={JSON.stringify(event)} />
  </RealtimeUserProvider>);
  expect(previous).not.toHaveBeenCalled();
  expect(replacement).toHaveBeenCalledExactlyOnceWith(event);
  expect(JSON.parse(screen.getByLabelText("subscriber").textContent ?? "").last).toEqual(event);
  expect(sockets).toHaveLength(1);
});

it("keeps the committed callback during a suspended transition, then publishes its replacement", async () => {
  const previous = vi.fn();
  const replacement = vi.fn();
  const attempted = vi.fn();
  let pending = true;
  let release: () => void = () => { throw new Error("Missing suspension resolver"); };
  const suspended = new Promise<void>((resolve) => { release = resolve; });
  function Gate({ active }: { active: boolean }) {
    if (active && pending) { attempted(); throw suspended; }
    return <span>{active ? "Committed replacement" : "Committed previous"}</span>;
  }
  function TransitionConsumer() {
    const [next, setNext] = useState(false);
    return <>
      <button onClick={() => { startTransition(() => { setNext(true); }); }}>Replace callback</button>
      <Suspense fallback={<span>Suspended</span>}>
        <Consumer name="subscriber" onEvent={next ? replacement : previous} /><Gate active={next} />
      </Suspense>
    </>;
  }
  render(<RealtimeUserProvider><TransitionConsumer /></RealtimeUserProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Replace callback" }));
  expect(attempted).toHaveBeenCalled();
  expect(screen.getByText("Committed previous")).toBeTruthy();
  const beforeCommit = { type: "notification", text: "Before commit" };
  act(() => { socket().receive(JSON.stringify(beforeCommit)); });
  expect(previous).toHaveBeenCalledExactlyOnceWith(beforeCommit);
  expect(replacement).not.toHaveBeenCalled();
  await act(async () => { pending = false; release(); await suspended; });
  expect(screen.getByText("Committed replacement")).toBeTruthy();
  const afterCommit = { type: "notification", text: "After commit" };
  act(() => { socket().receive(JSON.stringify(afterCommit)); });
  expect(previous).toHaveBeenCalledTimes(1);
  expect(replacement).toHaveBeenCalledExactlyOnceWith(afterCommit);
  expect(sockets).toHaveLength(1);
});

it("ignores malformed input and handles ping without changing the last event", () => {
  const callback = vi.fn();
  render(<RealtimeUserProvider><Consumer name="subscriber" onEvent={callback} /></RealtimeUserProvider>);
  const event = { type: "notification", text: "Retained" };
  act(() => {
    socket().receive(JSON.stringify(event));
    socket().receive("malformed JSON");
    socket().receive(JSON.stringify({ type: "ping" }));
  });
  expect(callback).toHaveBeenCalledExactlyOnceWith(event);
  expect(socket().send).toHaveBeenCalledExactlyOnceWith(JSON.stringify({ type: "pong" }));
  expect(JSON.parse(screen.getByLabelText("subscriber").textContent ?? "").last).toEqual(event);
});

it("reports disconnection and cancels reconnecting after provider unmount", () => {
  const view = render(<RealtimeUserProvider><Consumer name="subscriber" /></RealtimeUserProvider>);
  act(() => { socket().onopen?.(); socket().onclose?.(); });
  expect(JSON.parse(screen.getByLabelText("subscriber").textContent ?? "").connected).toBe(false);
  view.unmount();
  act(() => { vi.advanceTimersByTime(30_000); });
  expect(sockets).toHaveLength(1);
});

it("returns an idle snapshot outside the provider without connecting", () => {
  render(<Consumer name="outside" />);
  expect(JSON.parse(screen.getByLabelText("outside").textContent ?? "")).toEqual({ last: null, connected: false });
  expect(sockets).toHaveLength(0);
});
