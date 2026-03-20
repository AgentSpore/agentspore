"use client";

function cx(...classes: (string | undefined | false)[]) {
  return classes.filter(Boolean).join(" ");
}

// ─── Base Skeleton ─────────────────────────────────────────────────────────────

interface SkeletonProps {
  width?: string;
  height?: string;
  className?: string;
  rounded?: "sm" | "md" | "lg" | "xl" | "full";
}

const ROUNDED_MAP: Record<NonNullable<SkeletonProps["rounded"]>, string> = {
  sm:   "rounded-sm",
  md:   "rounded-md",
  lg:   "rounded-lg",
  xl:   "rounded-xl",
  full: "rounded-full",
};

export function Skeleton({
  width,
  height,
  className,
  rounded = "md",
}: SkeletonProps) {
  return (
    <>
      <style jsx global>{`
        @keyframes shimmer {
          0% { background-position: -200% center; }
          100% { background-position: 200% center; }
        }
        .skeleton-shimmer {
          background-image: linear-gradient(
            90deg,
            transparent 0%,
            rgba(64, 64, 64, 0.2) 50%,
            transparent 100%
          );
          background-size: 200% 100%;
          animation: shimmer 2s infinite ease-in-out;
        }
      `}</style>
      <div
        className={cx(
          "skeleton-shimmer bg-neutral-800/30",
          ROUNDED_MAP[rounded],
          className
        )}
        style={{
          width: width ?? undefined,
          height: height ?? undefined,
        }}
        aria-hidden="true"
      />
    </>
  );
}

// ─── SkeletonCard ──────────────────────────────────────────────────────────────

export function SkeletonCard({ className }: { className?: string }) {
  return <Skeleton rounded="xl" className={cx("w-full h-[120px]", className)} />;
}

// ─── SkeletonText ──────────────────────────────────────────────────────────────

const LINE_WIDTHS = ["100%", "92%", "85%", "78%", "60%"];

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cx("flex flex-col gap-2", className)} aria-hidden="true">
      {Array.from({ length: Math.max(1, lines) }).map((_, i) => (
        <Skeleton key={i} rounded="md" height="14px" width={LINE_WIDTHS[i % LINE_WIDTHS.length]} />
      ))}
    </div>
  );
}

// ─── SkeletonAvatar ────────────────────────────────────────────────────────────

const AVATAR_SIZE_MAP = { sm: "w-6 h-6", md: "w-8 h-8", lg: "w-10 h-10" };

export function SkeletonAvatar({ size = "md", className }: { size?: "sm" | "md" | "lg"; className?: string }) {
  return <Skeleton rounded="full" className={cx(AVATAR_SIZE_MAP[size], "shrink-0", className)} />;
}

// ─── SkeletonList ──────────────────────────────────────────────────────────────

export function SkeletonList({ items = 3, className }: { items?: number; className?: string }) {
  return (
    <div className={cx("flex flex-col gap-3", className)} aria-hidden="true">
      {Array.from({ length: Math.max(1, items) }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}
