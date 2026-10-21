/** 共享 UI 原语：Button / Field / Panel / Badge / SectionTitle。
    全部使用 Design Token，无硬编码颜色。 */

import { type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

type BtnVariant = "primary" | "secondary" | "ghost" | "danger";
type BtnSize = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BtnVariant;
  size?: BtnSize;
  full?: boolean;
}

export function Button({
  variant = "secondary",
  size = "md",
  full,
  className = "",
  type = "button",
  ...rest
}: ButtonProps) {
  const cls = [
    "cv-btn",
    `cv-btn--${variant}`,
    `cv-btn--${size}`,
    full ? "cv-btn--full" : "",
    className,
  ].filter(Boolean).join(" ");
  return <button type={type} className={cls} {...rest} />;
}

export function Field({
  label,
  hint,
  children,
}: {
  label?: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="cv-field">
      <span className="cv-field__label">{label}</span>
      {children}
      {hint ? <span className="cv-field__hint">{hint}</span> : null}
    </label>
  );
}

interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {}

export function TextInput({ className = "", ...rest }: TextInputProps) {
  return <input className={`cv-input ${className}`.trim()} {...rest} />;
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {}

export function Select({ className = "", ...rest }: SelectProps) {
  return <select className={`cv-select ${className}`.trim()} {...rest} />;
}

export function Panel({
  title,
  subtitle,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`cv-panel ${className}`.trim()}>
      <header className="cv-panel__head">
        <h2>{title}</h2>
        {subtitle ? <span className="cv-panel__sub">{subtitle}</span> : null}
      </header>
      {children}
    </section>
  );
}

type BadgeTone = "ok" | "warn" | "err" | "neutral" | "info" | "video" | "audio";

export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={`cv-badge cv-badge--${tone}`}>{children}</span>;
}

export function Row({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`cv-row ${className}`.trim()}>{children}</div>;
}

export function Mono({ children }: { children: ReactNode }) {
  return <code className="cv-mono">{children}</code>;
}