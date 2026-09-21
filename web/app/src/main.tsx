/** 应用入口：挂载到 #root。零业务，只装配全局样式与根组件。 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/global.css";
import "./styles/components.css";
import "./styles/panels.css";
import "./styles/feedback.css";
import "./styles/media.css";
import "./styles/layout.css";
import "./styles/player.css";
import "./styles/toolbar.css";
import "./styles/agent.css";
import "./styles/timeline.css";
import "./styles/timeline-track.css";
import "./styles/resources.css";
import "./styles/home.css";
import "./styles/templates.css";
import App from "./App";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root not found");

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
);