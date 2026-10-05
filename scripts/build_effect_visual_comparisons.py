"""Build a labelled comparison from actual before/after exported video frames."""
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from prepare_effect_visual_review import OUT,read_frames

def main():
    font=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",25)
    image=Image.new("RGB",(1296,864),"#17202b")
    draw=ImageDraw.Draw(image)
    for row,(slug,name) in enumerate((("circleMask","圆形聚焦：修复形状比例"),("boldPoster","色块网格：修复量化后网格缺失"))):
        draw.text((12,row*432+8),name,font=font,fill="white")
        paths=[OUT / f"reviews/fx-filter-before/cutvoke_preset_fx_{slug}-video-before.mp4",
               OUT / f"cases/cutvoke_preset_fx_{slug}-video/default.mp4"]
        for col,path in enumerate(paths):
            draw.text((12+col*644,row*432+43),"修复前" if col==0 else "修复后",font=font,fill="#c5d3e1")
            image.paste(read_frames(path,[45])[45],(8+col*644,row*432+78))
    image.save(OUT / "before-after.png")

if __name__=="__main__":main()
