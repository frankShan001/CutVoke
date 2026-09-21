/** 通用参数控件渲染器（J01）：把 effects.ts 的 ParamControl[] 渲染成表单。
 *  数据驱动——新增效果只要后端声明 parameters，前端无需改代码。 */
import type { ParamControl } from "../../lib/effectControls";

export function ParamControls({
  controls,
  onChange,
  onCommit,
}: {
  controls: ParamControl[];
  onChange: (name: string, value: unknown) => void;
  /** 数值输入失焦时提交（枚举/布尔即时提交）。 */
  onCommit?: () => void;
}) {
  if (controls.length === 0) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
      {controls.map((c) => (
        <div
          className="inspector__subrow"
          style={{ gap: 4, alignItems: "center" }}
          key={c.name}
        >
          <span className="inspector__key">{c.label}</span>
          {c.kind === "enum" ? (
            <select
              className="cv-input"
              style={{ height: 26 }}
              aria-label={c.label}
              value={String(c.value ?? "")}
              onChange={(e) => {
                onChange(c.name, e.target.value);
                onCommit?.();
              }}
            >
              {(c.options || []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          ) : c.kind === "boolean" ? (
            <input
              type="checkbox"
              aria-label={c.label}
              checked={!!c.value}
              onChange={(e) => {
                onChange(c.name, e.target.checked);
                onCommit?.();
              }}
            />
          ) : c.kind === "number" ? (
            <input
              type="number"
              className="cv-input"
              style={{ width: 76, height: 26 }}
              aria-label={c.label}
              value={c.value === undefined || c.value === null ? "" : String(c.value)}
              min={c.min}
              max={c.max}
              step={c.step}
              onChange={(e) => {
                const raw = e.target.value;
                onChange(c.name, raw === "" ? undefined : Number(raw));
              }}
              onBlur={() => onCommit?.()}
            />
          ) : (
            <input
              type="text"
              className="cv-input"
              style={{ height: 26 }}
              aria-label={c.label}
              value={String(c.value ?? "")}
              onChange={(e) => onChange(c.name, e.target.value)}
              onBlur={() => onCommit?.()}
            />
          )}
          {c.unit ? <span className="inspector__unit">{c.unit}</span> : null}
        </div>
      ))}
    </div>
  );
}
