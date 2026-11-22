import type { ReactElement } from "react";

const BASE = {
  viewBox: "0 0 24 24",
  width: "1em",
  height: "1em",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
  focusable: false,
} as const;

/** Decorative icons beside a visible stat label; colour comes from the parent via currentColor. */
export function AgentsIcon(): ReactElement {
  return (
    <svg {...BASE}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="4" fill="currentColor" />
    </svg>
  );
}

export function ProjectsIcon(): ReactElement {
  return (
    <svg {...BASE}>
      <polygon points="12 2 21 7 21 17 12 22 3 17 3 7" />
    </svg>
  );
}

export function CommitsIcon(): ReactElement {
  return (
    <svg {...BASE}>
      <path d="M3 6h5l8 12h5M14 6h7" />
    </svg>
  );
}

export function DeploysIcon(): ReactElement {
  return (
    <svg {...BASE}>
      <polygon points="12 4 21 20 3 20" fill="currentColor" />
    </svg>
  );
}

export function PullRequestsIcon(): ReactElement {
  return (
    <svg {...BASE}>
      <circle cx="6" cy="5" r="2" />
      <circle cx="6" cy="19" r="2" />
      <circle cx="18" cy="8" r="2" />
      <path d="M6 7v10M18 10c0 5-6 4-12 7" />
    </svg>
  );
}
