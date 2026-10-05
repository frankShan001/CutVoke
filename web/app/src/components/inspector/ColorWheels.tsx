/** Three color-balance wheels backed by the existing nine RGB parameters. */
import { useRef } from "react";

type Tone = "shadows" | "midtones" | "highlights";

const TONES: { id: Tone; label: string }[] = [
  { id: "shadows", label: "阴影" },
  { id: "midtones", label: "中间调" },
  { id: "highlights", label: "高光" },
];
const ROOT3 = Math.sqrt(3);

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function channels(params: Record<string, unknown>, tone: Tone): [number, number, number] {
  return (["R", "G", "B"] as const).map((channel) =>
    clamp(Number(params[`${tone}${channel}`] ?? 0) || 0, -1, 1),
  ) as [number, number, number];
}

function position(params: Record<string, unknown>, tone: Tone): [number, number] {
  const [r, g, b] = channels(params, tone);
  const x = (2 * r - g - b) / 3;
  const y = (b - g) / ROOT3;
  const radius = Math.hypot(x, y);
  return radius > 1 ? [x / radius, y / radius] : [x, y];
}

function withPosition(params: Record<string, unknown>, tone: Tone,
                      rawX: number, rawY: number): Record<string, unknown> {
  const radius = Math.hypot(rawX, rawY);
  const x = radius > 1 ? rawX / radius : rawX;
  const y = radius > 1 ? rawY / radius : rawY;
  const [r, g, b] = channels(params, tone);
  // Keep the common RGB component: dragging the wheel changes hue while
  // existing brightness adjustments remain available in numeric controls.
  const neutral = (r + g + b) / 3;
  const round = (value: number) => Math.round(clamp(value, -1, 1) * 1000) / 1000;
  return {
    ...params,
    [`${tone}R`]: round(neutral + x),
    [`${tone}G`]: round(neutral - x / 2 - ROOT3 * y / 2),
    [`${tone}B`]: round(neutral - x / 2 + ROOT3 * y / 2),
  };
}

function Wheel({ tone, label, params, onPreview, onCommit }: {
  tone: Tone;
  label: string;
  params: Record<string, unknown>;
  onPreview: (next: Record<string, unknown>) => void;
  onCommit: (next: Record<string, unknown>) => void;
}) {
  const startRef = useRef<Record<string, unknown> | null>(null);
  const draftRef = useRef<Record<string, unknown>>(params);
  const [x, y] = position(params, tone);
  const [r, g, b] = channels(params, tone);

  const point = (element: HTMLDivElement, clientX: number, clientY: number) => {
    const box = element.getBoundingClientRect();
    const half = Math.max(1, Math.min(box.width, box.height) / 2);
    return [(clientX - box.left - box.width / 2) / half,
            (clientY - box.top - box.height / 2) / half] as const;
  };
  const previewPoint = (element: HTMLDivElement, clientX: number, clientY: number) => {
    const [px, py] = point(element, clientX, clientY);
    const next = withPosition(startRef.current ?? params, tone, px, py);
    draftRef.current = next;
    onPreview(next);
  };

  return (
    <div className="color-wheel-group">
      <span className="color-wheel-group__name">{label}</span>
      <div className="color-wheel" role="slider" tabIndex={0}
        aria-label={`${label}色轮`} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={Math.round(Math.hypot(x, y) * 100)}
        aria-valuetext={`红 ${r.toFixed(2)}，绿 ${g.toFixed(2)}，蓝 ${b.toFixed(2)}`}
        title="拖动色点调整色偏；方向键微调，Home 归零"
        onPointerDown={(event) => {
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          startRef.current = params;
          previewPoint(event.currentTarget, event.clientX, event.clientY);
        }}
        onPointerMove={(event) => {
          if (startRef.current) previewPoint(event.currentTarget, event.clientX, event.clientY);
        }}
        onPointerUp={(event) => {
          if (!startRef.current) return;
          event.currentTarget.releasePointerCapture(event.pointerId);
          startRef.current = null;
          onCommit(draftRef.current);
        }}
        onPointerCancel={() => {
          if (startRef.current) onPreview(startRef.current);
          startRef.current = null;
        }}
        onKeyDown={(event) => {
          const step = event.shiftKey ? 0.01 : 0.05;
          let nx = x;
          let ny = y;
          if (event.key === "ArrowLeft") nx -= step;
          else if (event.key === "ArrowRight") nx += step;
          else if (event.key === "ArrowUp") ny -= step;
          else if (event.key === "ArrowDown") ny += step;
          else if (event.key === "Home") { nx = 0; ny = 0; }
          else return;
          event.preventDefault();
          const next = withPosition(params, tone, nx, ny);
          onPreview(next);
          onCommit(next);
        }}>
        <span className="color-wheel__point"
          style={{ left: `${(x + 1) * 50}%`, top: `${(y + 1) * 50}%` }} />
      </div>
      <button type="button" className="color-wheel-group__reset"
        onClick={() => {
          const next = { ...params, [`${tone}R`]: 0, [`${tone}G`]: 0, [`${tone}B`]: 0 };
          onPreview(next);
          onCommit(next);
        }}>归零</button>
    </div>
  );
}

export function ColorWheels({ params, onPreview, onCommit }: {
  params: Record<string, unknown>;
  onPreview: (next: Record<string, unknown>) => void;
  onCommit: (next: Record<string, unknown>) => void;
}) {
  return (
    <div className="color-wheels">
      <div className="color-wheels__grid">
        {TONES.map((tone) => <Wheel key={tone.id} tone={tone.id} label={tone.label}
          params={params} onPreview={onPreview} onCommit={onCommit} />)}
      </div>
      <p className="cv-hint">中心为中性；拖动色点并松手应用。亮度可在数值微调中继续调整。</p>
    </div>
  );
}
