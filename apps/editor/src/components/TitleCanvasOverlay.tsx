import { useEffect, useRef, useState, type PointerEvent } from "react";
import { tr } from "../i18n";
import { useEditor } from "../store/editor";
import { getLatestState, runCommand } from "../store/actions";
import { updateEffect } from "../store/effectEdit";
import { flushPendingEdits } from "../lib/pendingEdits";
import { rationalToSecs, secsToRational } from "../lib/rational";
import { titleLayout } from "../lib/titlePreview";

/** Native text stays sharp at every zoom. Video cache contains no root titles. */
export function TitleCanvasOverlay() {
  const { state, dispatch } = useEditor();
  const svg = useRef<SVGSVGElement>(null);
  const [fontVersion, setFontVersion] = useState(0);
  const [drag, setDrag] = useState<{ id: string; x: number; y: number; sx: number; sy: number;
    dx: number; dy: number; pointerId: number } | null>(null);
  const project = state.project, width = project?.sequence.width || 1920, height = project?.sequence.height || 1080;
  // Font loading changes both glyph pixels and hit bounds, without a server render.
  useEffect(() => {
    let mounted = true;
    const refresh = () => { if (mounted) setFontVersion(value => value + 1); };
    document.fonts.addEventListener("loadingdone", refresh);
    void document.fonts.ready.then(refresh);
    return () => { mounted = false; document.fonts.removeEventListener("loadingdone", refresh); };
  }, []);
  useEffect(() => { if (state.editLock) setDrag(null); }, [state.editLock]);
  void fontVersion;
  const begin = (event: PointerEvent<SVGGElement>, trackId: string, id: string, locked: boolean, anchorX: number, anchorY: number) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    dispatch({ type: "SELECTION_SET", selection: { trackId, clipId: id } });
    if (locked || state.editLock || !svg.current) return;
    svg.current.setPointerCapture(event.pointerId);
    setDrag({ id, x: anchorX / width, y: anchorY / height, sx: event.clientX, sy: event.clientY,
      dx: 0, dy: 0, pointerId: event.pointerId });
  };
  const move = (event: PointerEvent<SVGSVGElement>) => {
    if (!drag || drag.pointerId !== event.pointerId || !svg.current) return;
    const rect = svg.current.getBoundingClientRect();
    const dx = (event.clientX - drag.sx) / rect.width, dy = (event.clientY - drag.sy) / rect.height;
    if (Math.hypot(event.clientX - drag.sx, event.clientY - drag.sy) < 4) return;
    setDrag({ ...drag, dx: Math.max(-drag.x, Math.min(1 - drag.x, dx)), dy: Math.max(-drag.y, Math.min(1 - drag.y, dy)) });
  };
  const finish = async (event: PointerEvent<SVGSVGElement>) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const final = drag;
    if (!final.dx && !final.dy) { setDrag(null); return; }
    if (!await flushPendingEdits()) { setDrag(null); return; }
    const current = getLatestState() || state;
    if (current.currentId === project?.projectId && !current.editLock) {
      const clip = current.project?.sequence.tracks.flatMap(track => track.clips).find(item => item.id === final.id);
      if (clip?.keyframes?.x?.length || clip?.keyframes?.y?.length) {
        const time = secsToRational(Math.max(0, current.playhead - rationalToSecs(clip.timelineStart)));
        await runCommand(dispatch, current, "clip.keyframe", { clipId: final.id, action: "batch", keyframes: [
          { param: "x", time, value: (final.x + final.dx) * width },
          { param: "y", time, value: (final.y + final.dy) * height },
        ] });
      } else await updateEffect(dispatch, current, { clipId: final.id, effectId: "cutvoke.text",
        params: { x: final.x + final.dx, y: final.y + final.dy } });
    }
    setDrag(null);
  };
  return <svg ref={svg} className="player__title-overlay" viewBox={`0 0 ${width} ${height}`}
    aria-label={tr("画面文字")} onPointerMove={move} onPointerUp={event => void finish(event)}
    onPointerCancel={() => setDrag(null)}>
    {(project?.sequence.tracks || []).filter(track => track.kind === "text" && track.visible !== false).flatMap(track => track.clips.map(clip => ({ track, clip })))
      .sort((a, b) => rationalToSecs(a.clip.timelineStart) - rationalToSecs(b.clip.timelineStart) ||
        (a.clip.id < b.clip.id ? -1 : a.clip.id > b.clip.id ? 1 : 0)).map(({ track, clip }) => {
        if (clip.hidden || state.playhead + 1e-7 < rationalToSecs(clip.timelineStart) || state.playhead >= rationalToSecs(clip.timelineEnd)) return null;
        const canonical = clip.effects?.find(effect => effect.effectId === "cutvoke.text")?.params;
        if (!canonical) return null;
        const draft = !state.editLock ? state.titleDrafts[clip.id] : undefined;
        const p = { ...canonical, ...(draft && draft.projectId === project?.projectId ? draft.params : {}) };
        const layout = titleLayout(clip, p, state.playhead, width, height);
        const { glyphs, fontSize, family, anchor, baseline, metrics, advanced } = layout;
        const selected = state.selection?.clipId === clip.id;
        const unit = height / 1080, border = Number(p.strokeWidth ?? 2) * unit * (advanced ? .5 : 1);
        const outer = Number(p.outerStrokeWidth || 0) * unit, glow = Number(p.animGlow || 0) * unit;
        const dx = drag?.id === clip.id ? drag.dx * width : 0, dy = drag?.id === clip.id ? drag.dy * height : 0;
        return <g key={clip.id} data-title-clip-id={clip.id} data-selected={selected}
          role="button" tabIndex={0} aria-pressed={selected} aria-label={tr("选择文字：{0}", String(p.content || ""))}
          transform={`translate(${dx} ${dy})`} className="player__title-element"
          onPointerDown={event => begin(event, track.id, clip.id, !!track.locked, layout.x, layout.y)}
          onKeyDown={event => { if (event.key === "Enter" || event.key === " ") {
            event.preventDefault(); dispatch({ type: "SELECTION_SET", selection: { trackId: track.id, clipId: clip.id } });
          } }}>
          {glyphs.map((glyph, index) => {
            const left = anchor === "middle" ? -glyph.width / 2 : anchor === "end" ? -glyph.width : 0;
            const top = baseline - metrics.ascent * fontSize, h = metrics.height * fontSize;
            const shadow = Number(p.shadow ?? 1) * unit;
            const depthX = Number(p.blockShadowX || 0) * unit, depthY = Number(p.blockShadowY || 0) * unit;
            const common = { x: 0, y: baseline, fontFamily: family, fontSize, fontWeight: p.bold ? 700 : 400,
              textAnchor: anchor as "start" | "middle" | "end", paintOrder: "stroke fill" as const,
              strokeLinejoin: "round" as const, style: { whiteSpace: "pre" as const } };
            return <g key={index} transform={`translate(${glyph.x} ${glyph.y}) rotate(${-glyph.rotation}) scale(${glyph.scale} ${glyph.scale * Math.cos(glyph.flip * Math.PI / 180)})`}>
              <g opacity={glyph.opacity} style={{ filter: glyph.blur ? `blur(${glyph.blur * unit}px)` : undefined }} aria-hidden="true">
                {p.background && Number(p.panelWidth || 0) * Number(p.panelHeight || 0) === 0 ?
                  <rect x={left - border} y={top - border} width={glyph.width + border * 2} height={h + border * 2} fill={String(p.background)} /> : null}
                {p.background && index === 0 && Number(p.panelWidth) > 0 && Number(p.panelHeight) > 0 ?
                  <rect x={anchor === "middle" ? -Number(p.panelWidth) * width / 2 : anchor === "end" ? -Number(p.panelWidth) * width : 0}
                    y={advanced ? -Number(p.panelHeight) * height / 2 : -Number(p.panelHeight) * height + height * .025}
                    width={Number(p.panelWidth) * width} height={Number(p.panelHeight) * height} fill={String(p.background)} /> : null}
                {depthX || depthY ? <text {...common} transform={`translate(${depthX} ${depthY})`} fill={String(p.blockShadowColor || "#2b7183")} stroke={String(p.blockShadowColor || "#2b7183")} strokeWidth={(border + outer) * 2}>{glyph.text}</text> : null}
                {outer ? <text {...common} fill={String(p.outerStrokeColor || "#4b9fad")} stroke={String(p.outerStrokeColor || "#4b9fad")} strokeWidth={(border + outer) * 2}>{glyph.text}</text> : null}
                {glow ? <text {...common} fill="none" stroke={String(p.animColor || "#ff39d4")} strokeWidth={glow * 2} style={{ filter: `blur(${Math.max(1, glow * .6)}px)` }}>{glyph.text}</text> : null}
                {shadow ? <text {...common} transform={`translate(${shadow} ${shadow})`} fill="#000" stroke="#000" strokeWidth={border * 2}>{glyph.text}</text> : null}
                {layout.glitch ? [-1, 1].map(direction => <text key={direction} {...common} transform={`translate(${direction * 16 * Number(p.animStrength || 1) * unit} 0)`} fill={direction < 0 ? "#32f5ff" : "#ff39d4"}>{glyph.text}</text>) : null}
                <text {...common} fill={String(p.color || "#ffffff")} stroke={String(p.strokeColor || "#000000")} strokeWidth={border * 2}>{glyph.text}</text>
              </g>
              <rect x={left - 4 * unit} y={top - 4 * unit} width={Math.max(fontSize / 2, glyph.width) + 8 * unit} height={h + 8 * unit}
                fill="transparent" className={selected ? "player__title-selection" : "player__title-hit"} vectorEffect="non-scaling-stroke" />
              {selected ? [[left - 4 * unit, top - 4 * unit], [left + glyph.width + 4 * unit, top - 4 * unit],
                [left - 4 * unit, top + h + 4 * unit], [left + glyph.width + 4 * unit, top + h + 4 * unit]].map(([x, y], i) =>
                  <rect key={i} x={x - 3 * unit} y={y - 3 * unit} width={6 * unit} height={6 * unit} fill="#fff" stroke="#4d88ff" vectorEffect="non-scaling-stroke" pointerEvents="none" />) : null}
            </g>;
          })}
        </g>;
      })}
  </svg>;
}
