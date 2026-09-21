/** 花字（字幕标语）样式预设：纯前端预设，每个预设是一组 caption.update 的 style 字段。
 *  数据驱动——新增标语样式只需在此数组追加一项，FontStylePanel 自动渲染，无需改 UI。
 *  注意：preset.style 里的颜色是「字幕渲染用的领域数据」（发给后端 caption.update），
 *  不属于 UI 主题色，因此用具体色值；面板自身的 chrome 仍走 CSS 变量。 */

export type CaptionAlign = "left" | "center" | "right";

/** 字幕样式字段（与后端 caption.add/update 的 style 字段对齐）。 */
export interface CaptionStyle {
  fontSize: number;
  color: string;
  strokeColor: string;
  strokeWidth: number;
  bold: boolean;
  /** 字幕背景色；空串=无背景。 */
  background: string;
  align: CaptionAlign;
}

export interface FontStylePreset {
  id: string;
  name: string;
  description: string;
  style: CaptionStyle;
}

/** 清除样式时显式传回的默认值（后端 caption.update 缺省字段不变，故清除=显式置默认）。
 *  与后端 Caption 默认样式保持一致：白字、黑描边、居中、无背景。 */
export const CLEAR_CAPTION_STYLE: CaptionStyle = {
  fontSize: 0,
  color: "#ffffff",
  strokeColor: "#000000",
  strokeWidth: 0,
  bold: false,
  background: "",
  align: "center",
};

export const FONT_STYLE_PRESETS: FontStylePreset[] = [
  {
    id: "plain-white",
    name: "简洁白",
    description: "白字黑描边，通用清晰",
    style: {
      fontSize: 42,
      color: "#ffffff",
      strokeColor: "#000000",
      strokeWidth: 3,
      bold: false,
      background: "",
      align: "center",
    },
  },
  {
    id: "title-gold",
    name: "标题黄",
    description: "金黄大字加粗，适合片头标题",
    style: {
      fontSize: 64,
      color: "#FFD24A",
      strokeColor: "#000000",
      strokeWidth: 2,
      bold: true,
      background: "",
      align: "center",
    },
  },
  {
    id: "bold-stroke",
    name: "描边粗",
    description: "白字粗黑描边，远处也醒目",
    style: {
      fontSize: 48,
      color: "#ffffff",
      strokeColor: "#000000",
      strokeWidth: 8,
      bold: true,
      background: "",
      align: "center",
    },
  },
  {
    id: "bubble-bg",
    name: "气泡背景",
    description: "半透明黑底气泡，弱光场景可读",
    style: {
      fontSize: 40,
      color: "#ffffff",
      strokeColor: "#000000",
      strokeWidth: 0,
      bold: false,
      background: "rgba(0,0,0,0.55)",
      align: "center",
    },
  },
  {
    id: "neon",
    name: "霓虹",
    description: "青字品红描边，潮流卡点风",
    style: {
      fontSize: 46,
      color: "#00F0FF",
      strokeColor: "#FF2EC4",
      strokeWidth: 3,
      bold: true,
      background: "",
      align: "center",
    },
  },
  {
    id: "info-bar",
    name: "信息条",
    description: "深色信息条底，正文解说字幕",
    style: {
      fontSize: 34,
      color: "#ffffff",
      strokeColor: "#000000",
      strokeWidth: 0,
      bold: false,
      background: "#1E1E2E",
      align: "center",
    },
  },
];
