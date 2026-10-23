import { useEffect, useRef, useState } from "react";
import { Mic, Square } from "lucide-react";
import { tr } from "../i18n";
import { useEditor, showError } from "../store/editor";
import { getLatestState, runCommand } from "../store/actions";
import { uploadAsset } from "../lib/mediaApi";
import { addSessionAsset, sessionAssetFrom } from "../lib/assetStore";
import { secsToRational } from "../lib/rational";
import { Button, Field, Panel, Select } from "./ui";

async function wavFromRecording(blob: Blob): Promise<{ blob: Blob; duration: number }> {
  const context = new AudioContext();
  try {
    const buffer = await context.decodeAudioData(await blob.arrayBuffer());
    const channels = Math.min(2, buffer.numberOfChannels), size = buffer.length * channels * 2;
    const data = new ArrayBuffer(44 + size), view = new DataView(data);
    const text = (offset: number, value: string) => [...value].forEach((c, index) => view.setUint8(offset + index, c.charCodeAt(0)));
    text(0, "RIFF"); view.setUint32(4, size + 36, true); text(8, "WAVE"); text(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, channels, true);
    view.setUint32(24, buffer.sampleRate, true); view.setUint32(28, buffer.sampleRate * channels * 2, true);
    view.setUint16(32, channels * 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, size, true);
    for (let index = 0; index < buffer.length; index++) for (let channel = 0; channel < channels; channel++) {
      const sample = Math.max(-1, Math.min(1, buffer.getChannelData(channel)[index]));
      view.setInt16(44 + (index * channels + channel) * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
    }
    return { blob: new Blob([data], { type: "audio/wav" }), duration: buffer.duration };
  } finally { await context.close(); }
}

export function VoiceoverPanel() {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]), [device, setDevice] = useState("");
  const [countdown, setCountdown] = useState(3), [count, setCount] = useState<number | null>(null);
  const [sync, setSync] = useState(true), [phase, setPhase] = useState<"idle" | "countdown" | "waiting" | "recording" | "saving">("idle");
  const [take, setTake] = useState<{ blob: Blob; url: string; duration: number; at: number; projectId: string } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [monitor, setMonitor] = useState(false), [monitorVolume, setMonitorVolume] = useState(.5);
  const monitorContext = useRef<AudioContext | null>(null), monitorGain = useRef<GainNode | null>(null);
  const monitorSettings = useRef({ monitor, monitorVolume }); monitorSettings.current = { monitor, monitorVolume };
  const recorder = useRef<MediaRecorder | null>(null), stream = useRef<MediaStream | null>(null);
  const cancel = useRef(false), alive = useRef(true), timer = useRef<number | null>(null);
  const activeTake = useRef({ at: 0, projectId: "", sync: false, started: 0 });
  const activePhase = useRef(phase); activePhase.current = phase;

  const release = () => {
    stream.current?.getTracks().forEach(track => track.stop()); stream.current = null;
    monitorGain.current?.disconnect(); monitorGain.current = null;
    if (monitorContext.current) void monitorContext.current.close(); monitorContext.current = null;
  };
  useEffect(() => { if (monitorGain.current && monitorContext.current) monitorGain.current.gain.setValueAtTime(monitor ? monitorVolume : 0, monitorContext.current.currentTime); }, [monitor, monitorVolume]);
  const stop = () => {
    if (recorder.current?.state !== "inactive") recorder.current?.stop();
    if (timer.current) window.clearTimeout(timer.current);
    window.dispatchEvent(new Event("cutvoke:pause-play"));
  };
  useEffect(() => {
    alive.current = true;
    const clock = (event: Event) => {
      const detail = (event as CustomEvent<{ time: number; playing: boolean; end: number }>).detail;
      const rec = recorder.current;
      if (!activeTake.current.sync || !rec || rec.state === "inactive") return;
      if (detail.playing && rec.state === "paused") rec.resume();
      else if (!detail.playing && rec.state === "recording") rec.pause();
      if (detail.time >= detail.end - .02 && detail.end > activeTake.current.at && activePhase.current === "recording") stop();
    };
    window.addEventListener("cutvoke:transport-clock", clock);
    return () => { alive.current = false; cancel.current = true; stop(); release(); window.removeEventListener("cutvoke:transport-clock", clock); };
  }, []);
  useEffect(() => () => { if (take) URL.revokeObjectURL(take.url); }, [take]);
  useEffect(() => { if (activeTake.current.projectId && activeTake.current.projectId !== state.currentId) { cancel.current = true; stop(); release(); setTake(null); setPhase("idle"); } }, [state.currentId]);

  const chooseDevices = async () => {
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") throw new Error(tr("当前环境无法使用麦克风录音，请使用本机客户端或 localhost。"));
      const permission = await navigator.mediaDevices.getUserMedia({ audio: true });
      permission.getTracks().forEach(track => track.stop());
      setDevices((await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "audioinput"));
    } catch (error) { showError(dispatch, error); }
  };
  const record = async () => {
    if (!state.currentId || state.editLock) return;
    cancel.current = false; setTake(null); setElapsed(0);
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") throw new Error(tr("当前环境无法使用麦克风录音，请使用本机客户端或 localhost。"));
      window.dispatchEvent(new Event("cutvoke:pause-play"));
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: device ? { exact: device } : undefined, echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      if (cancel.current || !alive.current) { release(); return; }
      const context = new AudioContext(), gain = context.createGain();
      gain.gain.value = monitorSettings.current.monitor ? monitorSettings.current.monitorVolume : 0;
      context.createMediaStreamSource(stream.current).connect(gain).connect(context.destination);
      monitorContext.current = context; monitorGain.current = gain;
      await context.resume();
      setDevices((await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "audioinput"));
      setPhase("countdown");
      for (let value = countdown; value > 0; value--) {
        if (cancel.current) return;
        setCount(value); await new Promise(resolve => { timer.current = window.setTimeout(resolve, 1000); });
      }
      setCount(null);
      if (cancel.current) return;
      const latest = getLatestState() || state;
      const startedAt = latest.playhead;
      let anchor = startedAt;
      if (sync) {
        setPhase("waiting");
        anchor = await new Promise<number>((resolve, reject) => {
          const onClock = (event: Event) => {
            const detail = (event as CustomEvent<{ time: number; playing: boolean }>).detail;
            if (detail.playing) { window.clearTimeout(timeout); window.removeEventListener("cutvoke:transport-clock", onClock); resolve(detail.time); }
          };
          const timeout = window.setTimeout(() => { window.removeEventListener("cutvoke:transport-clock", onClock); reject(new Error(tr("画面尚未开始播放，请准备预览后重试。"))); }, 15000);
          window.addEventListener("cutvoke:transport-clock", onClock);
          window.dispatchEvent(new Event("cutvoke:toggle-play"));
        });
      }
      if (cancel.current || !stream.current) return;
      activeTake.current = { at: anchor, projectId: latest.currentId!, sync, started: performance.now() };
      const chunks: Blob[] = [];
      const mimeType = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"].find(type => MediaRecorder.isTypeSupported(type));
      const rec = new MediaRecorder(stream.current, mimeType ? { mimeType } : undefined);
      recorder.current = rec;
      rec.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      rec.onerror = () => { cancel.current = true; stop(); release(); setPhase("idle"); showError(dispatch, new Error(tr("麦克风录音失败，请检查设备权限后重试。"))); };
      rec.onstop = () => {
        release();
        if (cancel.current || !alive.current) return;
        setPhase("saving");
        void wavFromRecording(new Blob(chunks, { type: rec.mimeType })).then(result => {
          if (!alive.current || cancel.current) return;
          setTake({ ...result, url: URL.createObjectURL(result.blob), at: activeTake.current.at, projectId: activeTake.current.projectId });
          setPhase("idle");
        }).catch(error => { if (alive.current) { showError(dispatch, error); setPhase("idle"); } });
      };
      rec.start(250); setPhase("recording");
      timer.current = window.setTimeout(stop, 300000);
    } catch (error) { release(); if (alive.current) { showError(dispatch, error); setPhase("idle"); } }
  };
  useEffect(() => {
    if (phase !== "recording") return;
    const interval = window.setInterval(() => setElapsed((performance.now() - activeTake.current.started) / 1000), 100);
    return () => window.clearInterval(interval);
  }, [phase]);
  const insert = async () => {
    if (!take || !state.currentId || take.projectId !== state.currentId) return;
    setPhase("saving");
    try {
      const imported = await uploadAsset(new File([take.blob], `voiceover-${Date.now()}.wav`, { type: "audio/wav" }));
      if (imported.kind === "error") throw new Error(imported.message);
      addSessionAsset(sessionAssetFrom(imported.data, "audio", take.duration));
      const trackId = `voice_${crypto.randomUUID()}`;
      const result = await runCommand(dispatch, getLatestState() || state, "clip.insert", { trackId, createTrackKind: "audio",
        assetId: imported.data.assetId, sourceStart: secsToRational(0),
        timelineStart: secsToRational(take.at.toFixed(6)), timelineEnd: secsToRational((take.at + take.duration).toFixed(6)) });
      if (result.ok) setTake(null);
    } catch (error) { showError(dispatch, error); }
    finally { setPhase("idle"); }
  };
  const busy = phase !== "idle";
  return <Panel title={tr("配音录制")} subtitle={tr("录音从播放头位置插入，可试听后重录。") }>
    <Field label={tr("麦克风")}><Select value={device} onChange={event => setDevice(event.target.value)} disabled={busy} aria-label={tr("麦克风")}>
      <option value="">{tr("系统默认")}</option>{devices.map(item => <option key={item.deviceId} value={item.deviceId}>{item.label || item.deviceId}</option>)}
    </Select></Field>
    <Button onClick={() => void chooseDevices()} disabled={busy}>{tr("选择录音设备")}</Button>
    <Field label={tr("倒计时")}><Select value={countdown} onChange={event => setCountdown(Number(event.target.value))} disabled={busy} aria-label={tr("倒计时")}>
      {[0, 3, 5].map(value => <option key={value} value={value}>{value} s</option>)}
    </Select></Field>
    <label className="cv-row"><input type="checkbox" checked={sync} disabled={busy} onChange={event => setSync(event.target.checked)} />{tr("随画面播放录音")}</label>
    <label className="cv-row"><input type="checkbox" checked={monitor} onChange={event => setMonitor(event.target.checked)} />{tr("实时监听麦克风")}</label>
    {monitor ? <Field label={tr("监听音量")}><input type="range" min={0} max={1} step={.05} value={monitorVolume} onChange={event => setMonitorVolume(Number(event.target.value))} aria-label={tr("监听音量")} /></Field> : null}
    <p className="cv-hint">{tr("单次最多录制 5 分钟；监听建议使用耳机。")}</p>
    {busy && phase !== "saving" ? <Button variant="danger" onClick={() => { if (phase !== "recording") { cancel.current = true; release(); setPhase("idle"); } stop(); }}><Square size={14} />{tr("停止录音")}</Button>
      : <Button onClick={() => void record()} disabled={busy || !state.currentId || !!state.editLock}><Mic size={14} />{take ? tr("重录") : tr("开始录音")}</Button>}
    <output aria-live="polite">{count !== null && phase === "countdown" ? count : phase === "waiting" ? tr("等待画面播放…") : phase === "recording" ? `${elapsed.toFixed(1)} s` : ""}</output>
    {take ? <><audio controls src={take.url} style={{ width: "100%" }} /><p className="cv-hint">{tr("起点 {0} 秒 · 时长 {1} 秒", take.at.toFixed(2), take.duration.toFixed(2))}</p><Button variant="primary" full disabled={busy || !!state.editLock} onClick={() => void insert()}>{tr("添加配音到时间线")}</Button></> : null}
  </Panel>;
}
