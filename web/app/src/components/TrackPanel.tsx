/** 轨道控制面板：轨道列表（可删）+ 添加轨道 + 片段插入（智能时长探测）。 */

import { useState } from "react";
import { Clapperboard, Mic, Plus, AudioLines, Trash2, Crosshair } from "lucide-react";
import { Button, Field, Panel, Row, Select, TextInput } from "./ui";
import { useEditor, selectors, showError } from "../store/editor";
import { addTrack, insertClip } from "../store/actions";
import { removeTrack } from "../store/clipEdit";
import { trySecsToRational } from "../lib/rational";
import { ApiFailure } from "../lib/api";
import { probeMedia } from "../lib/mediaApi";

export function TrackPanel() {
  const { state, dispatch } = useEditor();
  const tracks = state.project?.sequence.tracks || [];
  const [kind, setKind] = useState<"video" | "audio">("video");
  const [autoProbe, setAutoProbe] = useState(true);

  // 片段表单状态
  const [srcPath, setSrcPath] = useState("");
  const [srcStart, setSrcStart] = useState("0");
  const [tlStart, setTlStart] = useState("0");
  const [tlEnd, setTlEnd] = useState("10");
  const [trackSel, setTrackSel] = useState("");

  const invalid = (message: string) =>
    new ApiFailure({ status: 400, code: "INVALID_ARGUMENT", message });

  const handleAddTrack = () => {
    void addTrack(dispatch, state, kind);
  };

  const handleRemoveTrack = (trackId: string) => {
    void removeTrack(dispatch, state, trackId).then((res) => {
      if (!res.ok && res.error?.message.includes("not empty")) {
        dispatch({
          type: "STATUS_SET",
          severity: "warn",
          text: `轨道 ${trackId} 非空：请先删除其片段再删轨道`,
        });
      }
    });
  };

  const handleInsert = async () => {
    if (!trackSel) {
      showError(dispatch, invalid("请先选择目标轨道"));
      return;
    }
    if (!srcPath.trim()) {
      showError(dispatch, invalid("请填写源路径 sourcePath"));
      return;
    }
    const timelineStart = trySecsToRational(tlStart);
    if (!timelineStart) {
      showError(dispatch, invalid("时间线起点无效：须为秒数字"));
      return;
    }

    let timelineEnd = trySecsToRational(tlEnd);
    let sourceStart = trySecsToRational(srcStart);
    if (!timelineEnd) {
      showError(dispatch, invalid("时间线终点无效：须为秒数字"));
      return;
    }

    // 智能时长：探测路径拿真实 duration 替代默认 10s
    if (autoProbe) {
      const probe = await probeMedia(srcPath.trim());
      if (probe.kind === "ok") {
        const dur = probe.data.duration;
        if (dur > 0) {
          const startSecs = Number(timelineStart.num) / Number(timelineStart.den);
          timelineEnd = trySecsToRational(startSecs + dur);
          sourceStart = trySecsToRational(0);
          dispatch({ type: "STATUS_SET", severity: "ok", text: `已探测时长 ${dur.toFixed(3)}s，片段到 ${(startSecs + dur).toFixed(3)}s` });
        }
      } else {
        dispatch({
          type: "STATUS_SET",
          severity: "warn",
          text: `未能探测时长（${probe.message}），按 10s 导入`,
        });
      }
    }

    if (!timelineEnd || !sourceStart) {
      showError(dispatch, invalid("时间输入无效"));
      return;
    }
    if (rationalSecs(timelineEnd) <= rationalSecs(timelineStart)) {
      showError(dispatch, invalid("时间线终点必须大于起点"));
      return;
    }

    void insertClip(dispatch, state, {
      trackId: trackSel,
      sourcePath: srcPath.trim(),
      sourceStart,
      timelineStart,
      timelineEnd,
    });
  };

  const tlEndPreview = trySecsToRational(tlEnd);
  const tlStartPreview = trySecsToRational(tlStart);
  const srcPreview = trySecsToRational(srcStart);
  const trackOptions = selectors.videoTracks(state.project)
    .concat(selectors.audioTracks(state.project));

  return (
    <>
      <Panel title="轨道" subtitle="track.add / track.remove">
        <div className="cv-row" style={{ alignItems: "flex-end" }}>
          <Field label="轨道类型">
            <Select value={kind} onChange={(e) => setKind(e.target.value as "video" | "audio")}>
              <option value="video">视频轨道</option>
              <option value="audio">音频轨道</option>
            </Select>
          </Field>
          <Button variant="primary" onClick={handleAddTrack} style={{ height: 30 }}>
            <Plus size={14} />
            添加
          </Button>
        </div>
        <div className="cv-tracks" style={{ marginTop: 8 }}>
          {tracks.length === 0 ? (
            <span className="cv-empty cv-empty--tight">暂无轨道</span>
          ) : (
            tracks.map((t) => (
              <div key={t.id} className="cv-track-row">
                {t.kind === "audio" ? <Mic size={13} className="cv-ic--audio" /> : <Clapperboard size={13} className="cv-ic--video" />}
                <span className="cv-mono cv-track-row__name">{t.id}</span>
                <span className={`cv-badge ${t.kind === "audio" ? "cv-badge--audio" : "cv-badge--video"}`}>
                  {t.kind}
                </span>
                <span className="cv-mono cv-track-row__count">{t.clips.length > 0 ? `${t.clips.length} 片段` : "空"}</span>
                <button
                  className="cv-btn cv-btn--sm cv-btn--ghost cv-track-row__del"
                  onClick={() => handleRemoveTrack(t.id)}
                  title="删除轨道（仅空轨）"
                  aria-label={`删除轨道 ${t.id}`}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))
          )}
        </div>
        <p className="cv-hint">仅可删除空轨；非空轨道需先删除其上片段。</p>
      </Panel>

      <Panel title="插入片段" subtitle="clip.insert · 智能时长">
        <Field label="目标轨道">
          <Select value={trackSel} onChange={(e) => setTrackSel(e.target.value)}>
            <option value="">
              {trackOptions.length ? "选择轨道…" : "（先添加轨道）"}
            </option>
            {trackOptions.map((t) => (
              <option key={t.id} value={t.id}>
                {t.id}（{t.kind}）
              </option>
            ))}
          </Select>
        </Field>
        <Field label="源路径 sourcePath">
          <TextInput
            value={srcPath}
            onChange={(e) => setSrcPath(e.target.value)}
            placeholder="本地媒体绝对路径"
          />
        </Field>
        <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 6 }}>
          <button
            className={`cv-chip ${autoProbe ? "cv-chip--on" : ""}`}
            onClick={() => setAutoProbe((v) => !v)}
            title="探测素材真实时长，替代默认 10s"
          >
            <Crosshair size={11} />
            探测时长（auto）
          </button>
          <span className="cv-hint" style={{ margin: 0 }}>{autoProbe ? "用真实时长" : "用手动时间"}</span>
        </div>
        <div style={{ marginTop: 8 }}>
          <Row>
            <Field label="时间线起点 (s)">
              <TextInput type="number" step="any" value={tlStart} onChange={(e) => setTlStart(e.target.value)} />
            </Field>
            <Field label="时间线终点 (s)">
              <TextInput type="number" step="any" value={tlEnd} onChange={(e) => setTlEnd(e.target.value)} />
            </Field>
          </Row>
        </div>
        <div style={{ marginTop: 4, display: "flex", justifyContent: "space-between" }}>
          <span className="cv-hint" style={{ marginTop: 0 }}>
            {tlStartPreview
              ? `起点 ${tlStartPreview.num}/${tlStartPreview.den}${tlEndPreview ? ` · 终点 ${tlEndPreview.num}/${tlEndPreview.den}` : ""}`
              : "起点/终点需为秒数字"}
          </span>
        </div>
        <div style={{ marginTop: 4 }}>
          <Row>
            <Field label="源起点 (s)">
              <TextInput type="number" step="any" value={srcStart} onChange={(e) => setSrcStart(e.target.value)} />
            </Field>
            <Field label="源">
              <div
                className="cv-mono"
                style={{
                  height: 30,
                  display: "flex",
                  alignItems: "center",
                  padding: "0 8px",
                  background: "var(--bg)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-md)",
                  color: srcPreview ? "var(--text-dim)" : "var(--err)",
                }}
              >
                {srcPreview ? `${srcPreview.num}/${srcPreview.den}` : "无效"}
              </div>
            </Field>
          </Row>
        </div>

        <Button variant="primary" full onClick={handleInsert} style={{ marginTop: 10 }}>
          <AudioLines size={14} />
          插入片段
        </Button>
        <p className="cv-hint">
          秒输入提交前自动转有理数 num/den（已约分）；探测失败时按 10s 导入。
        </p>
      </Panel>
    </>
  );
}

function rationalSecs(rt: { num: string; den: string }): number {
  return Number(rt.num) / Number(rt.den);
}