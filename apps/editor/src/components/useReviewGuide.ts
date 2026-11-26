import { useEffect, useRef, useState } from "react";
import { getReviewGuide, saveReviewGuide } from "../lib/api";
import { tr } from "../i18n";

type Guide = { sha256: string; pngBase64: string };

function readPng(file: File): Promise<string> {
  if (file.size > 4 * 1024 * 1024) return Promise.reject(new Error(tr("参考线 PNG 不能超过 4 MB")));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error(tr("读取参考线失败")));
    reader.readAsDataURL(file);
  });
}

export function useReviewGuide(projectId: string | undefined, sequenceId: string | undefined,
                               revision: string | undefined, width: number, height: number) {
  const target = JSON.stringify([projectId, sequenceId, width, height]);
  const generation = useRef(0);
  const targetRef = useRef(target);
  targetRef.current = target;
  const [state, setState] = useState<{ target: string; guide: Guide | null; shown: boolean; busy: boolean;
                                       error: string }>({ target: "", guide: null, shown: false, busy: true, error: "" });
  const current = state.target === target ? state : { target, guide: null, shown: false, busy: true, error: "" };

  useEffect(() => {
    if (!projectId || !sequenceId) return;
    const request = ++generation.current;
    const abort = new AbortController();
    setState({ target, guide: null, shown: false, busy: true, error: "" });
    getReviewGuide(projectId, sequenceId, abort.signal).then(({ guide }) => {
      if (!abort.signal.aborted && targetRef.current === target && generation.current === request)
        setState({ target, guide, shown: false, busy: false, error: "" });
    }).catch((error: Error) => {
      if (!abort.signal.aborted && targetRef.current === target && generation.current === request)
        setState({ target, guide: null, shown: false, busy: false, error: error.message });
    });
    return () => { abort.abort(); };
  }, [projectId, sequenceId, target]);

  async function replace(file: File | null) {
    if (!projectId || !sequenceId || !revision || current.busy) return;
    const request = ++generation.current;
    setState({ ...current, busy: true, error: "" });
    try {
      const png = file ? await readPng(file) : null;
      if (targetRef.current !== target || generation.current !== request) return;
      const { guide } = await saveReviewGuide(projectId, sequenceId, revision, png);
      if (targetRef.current === target && generation.current === request)
        setState({ target, guide, shown: !!guide, busy: false, error: "" });
    } catch (error) {
      if (targetRef.current === target && generation.current === request)
        setState({ ...current, busy: false, error: error instanceof Error ? error.message : String(error) });
    }
  }
  return {
    ...current,
    replace,
    toggle: () => setState({ ...current, shown: !current.shown }),
  };
}
