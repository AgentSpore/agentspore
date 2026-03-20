"use client";

function cx(...classes: (string | undefined | false)[]) {
  return classes.filter(Boolean).join(" ");
}

// Curated palette of 12 vibrant colors: [base, lighter 20% opacity border]
const COLOR_PALETTE = [
  { name: "violet",  from: "#7c3aed", to: "#a855f7", shadow: "112,58,237" },
  { name: "purple",  from: "#9333ea", to: "#c026d3", shadow: "147,51,234" },
  { name: "indigo",  from: "#4f46e5", to: "#6366f1", shadow: "79,70,229"  },
  { name: "cyan",    from: "#0891b2", to: "#06b6d4", shadow: "8,145,178"  },
  { name: "teal",    from: "#0f766e", to: "#14b8a6", shadow: "15,118,110" },
  { name: "emerald", from: "#059669", to: "#10b981", shadow: "5,150,105"  },
  { name: "lime",    from: "#65a30d", to: "#84cc16", shadow: "101,163,13" },
  { name: "amber",   from: "#d97706", to: "#f59e0b", shadow: "217,119,6"  },
  { name: "orange",  from: "#ea580c", to: "#f97316", shadow: "234,88,12"  },
  { name: "rose",    from: "#e11d48", to: "#f43f5e", shadow: "225,29,72"  },
  { name: "pink",    from: "#db2777", to: "#ec4899", shadow: "219,39,119" },
  { name: "fuchsia", from: "#c026d3", to: "#e879f9", shadow: "192,38,211" },
] as const;

// Deterministic hash: djb2 variant
function hash(str: string): number {
  let h = 5381;
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) + h) ^ str.charCodeAt(i);
    h = h >>> 0; // keep unsigned 32-bit
  }
  return h;
}

export function getAgentColors(nameOrId: string): {
  fromColor: string;
  toColor: string;
  shadowRgb: string;
  angle: number;
} {
  const h1 = hash(nameOrId);
  const h2 = hash(nameOrId + "_secondary");

  const primaryIndex = h1 % COLOR_PALETTE.length;
  // Ensure secondary color is always different from primary
  const secondaryIndex = (h1 + 1 + (h2 % (COLOR_PALETTE.length - 1))) % COLOR_PALETTE.length;

  const primary = COLOR_PALETTE[primaryIndex];
  const secondary = COLOR_PALETTE[secondaryIndex];

  // Gradient angle: 0–359°, snapped to 45° increments for a clean look
  const ANGLES = [45, 90, 135, 180, 225, 270, 315, 360] as const;
  const angle = ANGLES[h2 % ANGLES.length];

  return {
    fromColor: primary.from,
    toColor: secondary.from,
    shadowRgb: primary.shadow,
    angle,
  };
}

const SIZE_MAP = {
  sm: { px: 24, text: "text-[9px]",  radius: "rounded-md" },
  md: { px: 32, text: "text-[11px]", radius: "rounded-lg" },
  lg: { px: 40, text: "text-[13px]", radius: "rounded-lg" },
  xl: { px: 56, text: "text-[18px]", radius: "rounded-xl" },
} as const;

interface AgentAvatarProps {
  name: string;
  id?: string;
  size?: "sm" | "md" | "lg" | "xl";
  className?: string;
}

export default function AgentAvatar({
  name,
  id,
  size = "md",
  className,
}: AgentAvatarProps) {
  const seed = id ?? name;
  const { fromColor, toColor, shadowRgb, angle } = getAgentColors(seed);
  const { px, text, radius } = SIZE_MAP[size];

  const initials = name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0] ?? "")
    .join("")
    .toUpperCase()
    .slice(0, 2) || name.slice(0, 2).toUpperCase();

  const gradientStyle: React.CSSProperties = {
    width: px,
    height: px,
    minWidth: px,
    minHeight: px,
    background: `linear-gradient(${angle}deg, ${fromColor}, ${toColor})`,
    boxShadow: `inset 0 1px 3px rgba(255,255,255,0.15), 0 0 0 1px rgba(${shadowRgb},0.2), 0 2px 8px rgba(${shadowRgb},0.35)`,
    border: `1px solid rgba(${shadowRgb},0.2)`,
  };

  return (
    <div
      className={cx(
        radius,
        "flex items-center justify-center shrink-0 select-none overflow-hidden",
        className
      )}
      style={gradientStyle}
      aria-label={name}
      title={name}
    >
      <span
        className={cx(
          "font-mono font-bold text-white leading-none tracking-wide",
          text
        )}
        style={{ textShadow: "0 1px 2px rgba(0,0,0,0.3)" }}
      >
        {initials}
      </span>
    </div>
  );
}
