// Small shadcn-style primitives (hand-written; no network-installed generator).
import clsx from "clsx";
import { AlertOctagon, AlertTriangle, Info, Loader2, ShieldAlert, X } from "lucide-react";
import React, { useEffect } from "react";

export const cn = clsx;

type BtnVariant = "primary" | "secondary" | "ghost" | "danger";
export function Button({
  variant = "secondary",
  size = "md",
  className,
  loading,
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; size?: "sm" | "md"; loading?: boolean }) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        variant === "primary" && "bg-accent text-white hover:bg-accent-ink",
        variant === "secondary" && "border border-border bg-surface text-ink hover:bg-surface-2",
        variant === "ghost" && "text-ink-2 hover:bg-surface-2 hover:text-ink",
        variant === "danger" && "bg-critical text-white hover:opacity-90",
        className,
      )}
      disabled={loading || rest.disabled}
      {...rest}
    >
      {loading && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function Card({ className, children, title, actions }: { className?: string; children: React.ReactNode; title?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <section className={cn("rounded-lg border border-border bg-surface", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement> & { label?: string }) {
  const { label, className, id, ...rest } = props;
  const inputId = id ?? (label ? `in-${label.replace(/\W+/g, "-").toLowerCase()}` : undefined);
  return (
    <label className="flex flex-col gap-1 text-xs text-ink-2" htmlFor={inputId}>
      {label}
      <input
        id={inputId}
        className={cn("h-9 rounded-md border border-border bg-surface px-2.5 text-sm text-ink placeholder:text-ink-3", className)}
        {...rest}
      />
    </label>
  );
}

export function Select({ label, options, className, id, ...rest }: React.SelectHTMLAttributes<HTMLSelectElement> & { label?: string; options: Array<[string, string]> }) {
  const sid = id ?? (label ? `sel-${label.replace(/\W+/g, "-").toLowerCase()}` : undefined);
  return (
    <label className="flex flex-col gap-1 text-xs text-ink-2" htmlFor={sid}>
      {label}
      <select id={sid} className={cn("h-9 rounded-md border border-border bg-surface px-2 text-sm text-ink", className)} {...rest}>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Textarea({ label, className, ...rest }: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label?: string }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-ink-2">
      {label}
      <textarea className={cn("rounded-md border border-border bg-surface p-2 text-sm text-ink", className)} {...rest} />
    </label>
  );
}

const TIER_STYLE: Record<string, { cls: string; Icon: React.ComponentType<{ className?: string }>; label: string }> = {
  HARD: { cls: "border-critical/40 bg-critical/10 text-critical", Icon: AlertOctagon, label: "Hard" },
  PROBABLE: { cls: "border-serious/50 bg-serious/10 text-[#9a3f17]", Icon: ShieldAlert, label: "Probable" },
  WEAK: { cls: "border-warning/60 bg-warning/10 text-[#7a4d00]", Icon: AlertTriangle, label: "Weak" },
  INFO: { cls: "border-border bg-surface-2 text-ink-2", Icon: Info, label: "Info" },
};

/** Tier = status: always icon + label, never color alone. */
export function TierBadge({ tier }: { tier: string }) {
  const s = TIER_STYLE[tier] ?? TIER_STYLE.INFO;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium", s.cls)}>
      <s.Icon className="size-3.5" aria-hidden />
      {s.label}
    </span>
  );
}

export function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "good" | "accent" | "warn" | "bad" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium",
        tone === "neutral" && "bg-surface-2 text-ink-2",
        tone === "good" && "bg-good/10 text-good-ink",
        tone === "accent" && "bg-accent/10 text-accent-ink",
        tone === "warn" && "bg-warning/15 text-[#7a4d00]",
        tone === "bad" && "bg-critical/10 text-critical",
      )}
    >
      {children}
    </span>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-4 text-sm text-ink-2" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-md border border-dashed border-border p-8 text-center text-sm text-ink-3">{children}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div role="alert" className="rounded-md border border-critical/40 bg-critical/5 p-3 text-sm text-critical">
      {error instanceof Error ? error.message : String(error)}
    </div>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6" role="dialog" aria-modal="true" aria-label={title}>
      <div className={cn("max-h-full overflow-auto rounded-lg border border-border bg-surface shadow-xl", wide ? "w-[min(1100px,100%)]" : "w-[min(560px,100%)]")}>
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="font-semibold">{title}</h2>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close">
            <X className="size-4" />
          </Button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: Array<[T, string]>; value: T; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="flex gap-1 border-b border-border">
      {tabs.map(([v, l]) => (
        <button
          key={v}
          role="tab"
          aria-selected={value === v}
          onClick={() => onChange(v)}
          className={cn("-mb-px border-b-2 px-3 py-2 text-sm", value === v ? "border-accent font-medium text-ink" : "border-transparent text-ink-2 hover:text-ink")}
        >
          {l}
        </button>
      ))}
    </div>
  );
}

export function Table({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-auto rounded-md border border-border", className)}>
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  );
}
export const Th = ({ children, className, ...r }: React.ThHTMLAttributes<HTMLTableCellElement>) => (
  <th className={cn("sticky top-0 border-b border-border bg-surface-2 px-3 py-2 text-left text-xs font-medium text-ink-2", className)} {...r}>
    {children}
  </th>
);
export const Td = ({ children, className, ...r }: React.TdHTMLAttributes<HTMLTableCellElement>) => (
  <td className={cn("border-b border-border px-3 py-2 align-top", className)} {...r}>
    {children}
  </td>
);

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {subtitle && <p className="text-sm text-ink-2">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-2">{actions}</div>
    </div>
  );
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="rounded border border-border bg-surface-2 px-1 font-mono text-[11px] text-ink-2">{children}</kbd>;
}
