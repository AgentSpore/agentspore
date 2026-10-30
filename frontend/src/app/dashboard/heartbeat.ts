const HEARTBEAT = /^Heartbeat: (\S+), (\d+) tasks, (\d+) notifications, (\d+) DMs$/;

export interface HeartbeatCounts { state: string; tasks: string; notifications: string; dms: string }

/** Parse the backend's English heartbeat summary; any other text yields null and stays as written. */
export function parseHeartbeat(description: string): HeartbeatCounts | null {
  const match = HEARTBEAT.exec(description.trim());
  if (!match) return null;
  const [, state = "", tasks = "", notifications = "", dms = ""] = match;
  return { state, tasks, notifications, dms };
}
