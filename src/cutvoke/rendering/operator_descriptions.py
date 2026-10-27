"""Parameter guidance for the deterministic composition operators."""

DESCRIPTIONS = {
    'seed': ('随机种子；相同值重现同一噪声场', 'Random seed; the same value reproduces the same noise field'),
    'scale': ('噪声单元大小，相对画面短边', 'Noise cell size as a fraction of the shorter canvas edge'),
    'octaves': ('叠加噪声层数；越高细节越丰富', 'Number of summed noise octaves; higher values add fine detail'),
    'evolution': ('每秒噪声演化速度；负数反向', 'Noise evolution per second; negative values reverse it'),
    'contrast': ('噪声相对中灰的对比倍率', 'Noise contrast multiplier around middle gray'),
    'mix': ('噪声与源画面的混合占比', 'Fraction of noise mixed into the original picture'),
    'amount': ('最大像素置换距离', 'Maximum displacement in canvas pixels'),
    'amplitude': ('波浪位移幅度，像素', 'Wave displacement amplitude in pixels'),
    'wavelength': ('一个完整波长的像素长度', 'Length of one wave cycle in pixels'),
    'frequency': ('每秒波浪周期数；负数反向', 'Wave cycles per second; negative values reverse direction'),
    'phase': ('波浪初始相位，角度', 'Initial wave phase in degrees'),
    'axis': ('波浪位移方向，横向或纵向', 'Horizontal or vertical displacement direction'),
    'points': ('路径锚点及可选贝塞尔手柄，归一化画布坐标', 'Path anchors and optional Bezier handles in normalized canvas coordinates'),
    'pathFrames': ('按片段内秒数排列的跟踪轮廓与置信度', 'Tracked contours and confidence ordered by clip-local seconds'),
    'feather': ('轮廓羽化宽度，相对画面短边', 'Contour feather width as a fraction of the shorter canvas edge'),
    'invert': ('反转蒙版保留和遮挡的区域', 'Invert the areas retained and removed by the matte'),
    'closed': ('连接最后和第一个锚点并填充内部', 'Connect the last anchor to the first and fill the enclosed area'),
    'strokeColor': ('路径描边颜色，十六进制 RGB', 'Path stroke color as hexadecimal RGB'),
    'fillColor': ('闭合路径内部填充颜色，十六进制 RGB', 'Closed-path fill color as hexadecimal RGB'),
    'strokeWidth': ('描边宽度，相对画面短边', 'Stroke width as a fraction of the shorter canvas edge'),
    'fillOpacity': ('内部填充不透明度，0 透明、1 不透明', 'Interior fill opacity, from transparent zero to opaque one'),
    'trimStart': ('保留路径的起点，0 到 1 的路径长度比例', 'Start of the retained path as a fraction of total path length'),
    'trimEnd': ('保留路径的终点，0 到 1 的路径长度比例', 'End of the retained path as a fraction of total path length'),
    'trimFrames': ('片段内秒数及对应路径起止比例的动画关键点', 'Animation keys containing clip-local seconds and retained path fractions'),
    'repeaterCount': ('重复绘制的路径总数', 'Total number of repeated path copies'),
    'offsetX': ('每次重复的横向偏移，相对画面宽度', 'Horizontal offset per repeated copy as a fraction of canvas width'),
    'offsetY': ('每次重复的纵向偏移，相对画面高度', 'Vertical offset per repeated copy as a fraction of canvas height'),
    'repeaterRotation': ('每次重复增加的旋转角度', 'Additional rotation in degrees per repeated copy'),
    'repeaterScale': ('每次重复相对前一次的缩放倍率', 'Scale multiplier relative to the previous repeated copy'),
}


def describe(properties):
    for key, value in properties.items():
        if key in DESCRIPTIONS:
            value['description'] = dict(zip(('zh-CN', 'en'), DESCRIPTIONS[key]))
    return properties
