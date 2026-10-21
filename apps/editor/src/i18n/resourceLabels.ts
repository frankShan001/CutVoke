import { tr } from ".";

/** Registry keys remain stable in filtering and API payloads. Only labels change. */
export function subcategoryLabel(key: string): string {
  switch (key) {
    case "基础": return tr("基础");
    case "入场": return tr("入场");
    case "出场": return tr("出场");
    case "循环": return tr("循环");
    case "组合": return tr("组合");
    case "叠化": return tr("叠化");
    case "擦除": return tr("擦除");
    case "故障": return tr("故障");
    case "运镜": return tr("运镜");
    case "动感": return tr("动感");
    case "光影": return tr("光影");
    case "调色": return tr("调色");
    case "风格": return tr("风格");
    case "构图": return tr("构图");
    case "音频": return tr("音频");
    case "普通文字": return tr("普通文字");
    case "花字": return tr("花字");
    case "文字动画": return tr("文字动画");
    case "人物发光": return tr("人物发光");
    case "人物描边": return tr("人物描边");
    case "人物分身": return tr("人物分身");
    default: return key;
  }
}
