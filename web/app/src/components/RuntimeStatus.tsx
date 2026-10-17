import { useEffect, useState } from "react";
import { tr, useLocale } from "../i18n";

/** Old services must not silently present schemas from an earlier build. */
export function RuntimeStatus() {
  useLocale();
  const [outdated, setOutdated] = useState(false);
  useEffect(() => {
    let disposed = false;
    const check = async () => {
      try {
        const response = await fetch("/api/v1/runtime");
        const info = await response.json();
        if (!disposed && response.ok) setOutdated(info.restartRequired === true);
      } catch { /* The ordinary connection status reports network failures. */ }
    };
    void check();
    const timer = window.setInterval(() => void check(), 10000);
    return () => { disposed = true; clearInterval(timer); };
  }, []);
  return outdated ? <span role="alert" className="topbar__runtime-warning"
    title={tr("源码已更新，请结束或取消运行中的任务，然后重启 CutVoke 服务并重新连接 MCP。")}>{tr("服务需要重启")}</span> : null;
}
