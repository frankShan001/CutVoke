"""Executed inside the downloaded isolated interpreter, without host packages."""
import argparse
import json
from pathlib import Path
import sys
import time


def run(extension,root,backend):
    import numpy as np
    started=time.monotonic();root=Path(root)
    if extension=='person':
        import mediapipe as mp
        image=mp.Image(image_format=mp.ImageFormat.SRGB,data=np.zeros((256,256,3),np.uint8))
        vision=mp.tasks.vision
        options=vision.ImageSegmenterOptions(base_options=mp.tasks.BaseOptions(model_asset_path=str(root/'models'/'selfie_segmenter.tflite')),output_confidence_masks=True)
        with vision.ImageSegmenter.create_from_options(options) as model:
            result=model.segment(image)
            assert result.confidence_masks,'person model returned no mask'
        options=vision.InteractiveSegmenterLegacyOptions(base_options=mp.tasks.BaseOptions(model_asset_path=str(root/'models'/'magic_touch.tflite')),output_confidence_masks=True)
        from mediapipe.tasks.python.components.containers.keypoint import NormalizedKeypoint
        roi=vision.InteractiveSegmenterLegacyRegionOfInterest(format=vision.InteractiveSegmenterLegacyRegionOfInterest.Format.KEYPOINT,keypoint=NormalizedKeypoint(x=.5,y=.5))
        with vision.InteractiveSegmenterLegacy.create_from_options(options) as model:assert model.segment(image,roi).confidence_masks
        versions={'mediapipe':mp.__version__};device='cpu';frames=2
    else:
        import torch
        from PIL import Image,ImageDraw
        from sam2.build_sam import build_sam2_video_predictor
        device='cuda' if backend=='cuda128' else 'cpu'
        if device=='cuda':
            if not torch.cuda.is_available():raise RuntimeError('the selected GPU runtime cannot use this GPU/driver')
            assert torch.ones((32,32),device='cuda').sum().item()==1024
        frames_dir=root/'probe-frames';frames_dir.mkdir(exist_ok=True)
        for index in range(2):
            image=Image.new('RGB',(128,128),'#20304a');ImageDraw.Draw(image).ellipse((36+index*2,36,92+index*2,92),fill='#ffc030');image.save(frames_dir/f'{index:08d}.jpg')
        predictor=build_sam2_video_predictor('configs/sam2.1/sam2.1_hiera_b+.yaml',str(root/'models'/'sam2.1_hiera_base_plus.pt'),device=device,apply_postprocessing=False)
        with torch.inference_mode():
            state=predictor.init_state(video_path=str(frames_dir),offload_video_to_cpu=True)
            predictor.add_new_points_or_box(state,frame_idx=0,obj_id=1,points=np.asarray([[64,64]],np.float32),labels=np.asarray([1],np.int32))
            frames=sum(1 for _,_,masks in predictor.propagate_in_video(state) if bool((masks>0).any()))
        if frames!=2:raise RuntimeError('local model did not produce both smoke-test masks')
        versions={'torch':torch.__version__};predictor.reset_state(state)
    return {'pythonVersion':sys.version.split()[0],'executable':sys.executable,'backend':device,'versions':versions,
            'smokeFrames':frames,'smokeSeconds':round(time.monotonic()-started,3),'qualityBenchmark':False,'userSiteEnabled':False}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--extension',required=True);parser.add_argument('--root',required=True);parser.add_argument('--backend',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=run(args.extension,args.root,args.backend)
    Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf8')


if __name__=='__main__':main()
