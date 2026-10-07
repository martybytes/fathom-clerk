// Small hand-rolled primitives. No component library: this is six screens, and
// a dependency would be more surface than the thing it replaces.

import type { ReactNode } from "react";
import { ICON_PATHS } from "./icons";

/** Inline SVG, not an icon font.
 *
 *  The full Material Symbols woff2 is 3.8 MB and this app uses about
 *  twenty-five glyphs; since web/dist is committed, shipping the font would put
 *  that binary in git. The generated path set is 10 kB and needs no font load.
 *  Size follows the surrounding font-size, so the existing text-[18px] classes
 *  keep working unchanged.
 */
export function Icon({ name, className = "" }: { name: string; className?: string }) {
  const path = ICON_PATHS[name];
  if (!path) return null;
  return (
    <svg
      viewBox="0 -960 960 960"
      className={`inline-block h-[1em] w-[1em] shrink-0 fill-current ${className}`}
      aria-hidden="true"
      focusable="false"
    >
      <path d={path} />
    </svg>
  );
}

export function Panel({
  title,
  icon,
  children,
  actions,
  className = "",
}: {
  title?: string;
  icon?: string;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-surface0 bg-mantle p-4 ${className}`}
    >
      {(title || actions) && (
        <header className="mb-3 flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-mauve">
            {icon && <Icon name={icon} className="text-[18px]" />}
            {title}
          </h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  icon,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  icon?: string;
  title?: string;
}) {
  const styles = {
    primary: "bg-blue text-base hover:brightness-110",
    danger: "bg-red text-base hover:brightness-110",
    ghost: "bg-transparent text-subtext hover:bg-surface0 hover:text-text",
    default: "bg-surface0 text-text hover:bg-surface1",
  }[variant];
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${styles}`}
    >
      {icon && <Icon name={icon} className="text-[18px]" />}
      {children}
    </button>
  );
}

export function Field({
  label,
  note,
  source,
  children,
}: {
  label: string;
  note?: string;
  source?: string;
  children: ReactNode;
}) {
  return (
    <div className="border-b border-surface0/60 py-3 last:border-0">
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <label className="text-sm font-medium">{label}</label>
        {source && <SourceTag source={source} />}
      </div>
      {children}
      {note && <p className="mt-1.5 text-xs leading-relaxed text-overlay">{note}</p>}
    </div>
  );
}

/** Which layer a value came from. A value that is right for the wrong reason is
 *  the one that bites later, so the UI always says. */
export function SourceTag({ source }: { source: string }) {
  const styles: Record<string, string> = {
    "config.json": "text-yellow",
    "local.json": "text-peach",
    default: "text-overlay",
  };
  const label = source === "default" ? "default" : `set in ${source}`;
  return <span className={`text-[11px] ${styles[source] ?? "text-overlay"}`}>{label}</span>;
}

export function Stat({
  label,
  value,
  tone = "text",
  hint,
}: {
  label: string;
  value: ReactNode;
  tone?: "text" | "green" | "red" | "yellow";
  hint?: string;
}) {
  const colour = { text: "text-text", green: "text-green", red: "text-red", yellow: "text-yellow" }[
    tone
  ];
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-overlay">{label}</div>
      <div className={`mt-0.5 text-lg font-semibold ${colour}`}>{value}</div>
      {hint && <div className="text-xs text-overlay">{hint}</div>}
    </div>
  );
}

export function Toast({
  message,
  tone,
  onDismiss,
}: {
  message: string;
  tone: "ok" | "error";
  onDismiss: () => void;
}) {
  return (
    <div
      className={`fixed bottom-4 right-4 z-50 flex max-w-md items-start gap-2 rounded-lg border px-4 py-3 text-sm shadow-lg ${
        tone === "ok"
          ? "border-green/40 bg-mantle text-green"
          : "border-red/40 bg-mantle text-red"
      }`}
    >
      <Icon name={tone === "ok" ? "check_circle" : "error"} className="text-[18px]" />
      <span className="flex-1">{message}</span>
      <button type="button" onClick={onDismiss} className="text-overlay hover:text-text">
        <Icon name="close" className="text-[16px]" />
      </button>
    </div>
  );
}

export function Empty({
  icon,
  image,
  title,
  hint,
}: {
  icon?: string;
  image?: string;
  title: string;
  hint?: string;
}) {
  return (
    <div className="flex flex-col items-center gap-2 py-12 text-center text-overlay">
      {/* Decorative: the title beside it already says everything. */}
      {image ? (
        <img src={image} alt="" className="mb-1 h-32 w-32 rounded-xl" />
      ) : (
        icon && <Icon name={icon} className="text-[40px]" />
      )}
      <p className="text-sm">{title}</p>
      {hint && <p className="max-w-sm text-xs">{hint}</p>}
    </div>
  );
}

export function Spinner() {
  return (
    <Icon name="progress_activity" className="animate-spin text-[18px] text-blue" />
  );
}
