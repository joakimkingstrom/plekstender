import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Mark } from "./Mark";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-on-accent hover:bg-accent-strong",
  secondary: "bg-surface-2 text-text hover:bg-border",
  ghost: "text-muted hover:text-text hover:bg-surface-2",
  danger: "text-danger hover:bg-danger/10",
};

// Padding, text size and gap for each size of button. Set them with `size`, not in
// className: two classes for the same property don't reliably override each other.
const sizes = {
  xs: "gap-1 px-2 py-1 text-xs",
  sm: "gap-1.5 px-3 py-1.5 text-xs",
  md: "gap-2 px-4 py-2 text-sm",
  lg: "gap-2 px-4 py-3 text-sm",
  none: "gap-2", // className sets them
};

export function Button({
  variant = "secondary",
  size = "md",
  busy = false,
  className = "",
  children,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: keyof typeof sizes;
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center rounded-lg font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${sizes[size]} ${variants[variant]} ${className}`}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...props}
    >
      {busy && <Spinner small />}
      {children}
    </button>
  );
}

export function Card({
  title,
  action,
  children,
  className = "",
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-2xl border border-border bg-surface p-5 ${className}`}
    >
      {(title || action) && (
        <header className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

/** Loading: the brand's spinning pick (the same turn as clicking the logo). */
export function Spinner({ small = false, large = false }: { small?: boolean; large?: boolean }) {
  const size = large ? "size-14" : small ? "size-4" : "size-7";
  return (
    <span
      role="status"
      aria-label="Loading"
      className={`inline-flex shrink-0 ${size}`}
    >
      <Mark className="pick-spin size-full" />
    </span>
  );
}

export function Notice({
  tone = "info",
  children,
}: {
  tone?: "info" | "error" | "warn";
  children: ReactNode;
}) {
  const tones = {
    info: "border-border bg-surface-2 text-text",
    error: "border-danger/40 bg-danger/10 text-danger",
    warn: "border-warn/40 bg-warn/10 text-warn",
  };
  return (
    <div
      role={tone === "error" ? "alert" : undefined}
      className={`rounded-lg border px-4 py-3 text-sm ${tones[tone]}`}
    >
      {children}
    </div>
  );
}

const DOT = {
  ok: "bg-ok",
  warning: "bg-warn",
  error: "bg-danger",
  off: "bg-border",
} as const;

export function StatusDot({
  ok,
  tone,
  label,
}: {
  ok?: boolean;
  tone?: keyof typeof DOT;
  label: string;
}) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted">
      <span
        className={`size-2 shrink-0 rounded-full ${DOT[tone ?? (ok ? "ok" : "error")]}`}
        aria-hidden
      />
      {label}
    </span>
  );
}

export function Avatar({ url, name }: { url?: string | null; name: string }) {
  if (url) {
    return (
      <img
        src={url}
        alt=""
        className="size-8 rounded-full object-cover"
        referrerPolicy="no-referrer"
      />
    );
  }
  return (
    <span className="flex size-8 items-center justify-center rounded-full bg-surface-2 text-sm font-semibold">
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

export function Badge({
  children,
  tone = "muted",
}: {
  children: ReactNode;
  tone?: "muted" | "accent" | "ok";
}) {
  const tones = {
    muted: "bg-surface-2 text-muted",
    accent: "bg-accent/15 text-accent",
    ok: "bg-ok/15 text-ok",
  };
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function PageTitle({
  children,
  subtitle,
}: {
  children: ReactNode;
  subtitle?: ReactNode;
}) {
  return (
    <div className="mb-6">
      <h1 className="text-2xl font-bold tracking-tight">{children}</h1>
      {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
    </div>
  );
}

/** A curved "go back" arrow for undo buttons (the ↶ character is missing from many fonts). */
export function UndoIcon({ className = "size-3.5" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={`shrink-0 ${className}`}
    >
      <path d="M5.5 3 2.5 6l3 3" />
      <path d="M2.5 6h7a4 4 0 0 1 0 8H7" />
    </svg>
  );
}
