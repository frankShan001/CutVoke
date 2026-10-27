import copy
from pathlib import Path
import shutil

import numpy as np
from PIL import Image

from cutvoke.domain.protocol import Command
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService
from cutvoke.storage.project_directory import ProjectLibraryStore, DirectoryProjectStore
from cutvoke.storage.projectpack import pack_project, unpack_project


def test_linked_composition_survives_package_and_independent_directory_move(tmp_path):
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    Image.new("RGB", (96, 64), (200, 70, 20)).save(first)
    image = Image.new("RGBA", (96, 64), (255, 255, 255, 0))
    image.paste((255, 255, 255, 255), (0, 0, 48, 64)); image.save(second)
    library = ProjectLibraryStore(str(tmp_path / "library.sqlite"))
    service = EditService(library)
    renderer = RenderService(gpu_effects=False)
    project = service.create_project("portable", width=96, height=64, fps=R.of(10))
    def command(kind, **payload):
        return service.execute(Command(kind, payload, project_id="portable", expected_revision=service.get_project("portable").revision))
    try:
        command("clip.insert", clipId="picture", trackId="v1", createTrackKind="video", sourcePath=str(first), timelineEnd=R.of(3).to_json())
        command("clip.insert", clipId="matte", trackId="v2", createTrackKind="video", sourcePath=str(second), timelineEnd=R.of(3).to_json())
        command("clip.composite", clipId="matte", compositing={"matteOnly": True})
        command("clip.composite", clipId="picture", compositing={"matte": {"clipId": "matte"}})
        project = service.get_project("portable")
        master = copy.deepcopy(project.sequence); master.id = "master"
        master.exposed_properties = [{"id": "visibility", "label": "Visibility", "clipId": "picture", "param": "opacity"}]
        project.sequences.append(master); library.commit(project)
        command("composition.instance", clipId="instance", sequenceId="master", timelineStart=3, timelineEnd=6)
        command("composition.override", clipId="instance", values={"visibility": .5})
        command("clip.bind", clipId="picture", param="opacity", binding={"source": {"type": "constant", "value": .75}})
        project = service.get_project("portable")
        expected = tmp_path / "expected.png"; renderer.extract_frame(project, 3.5, str(expected))
        package = tmp_path / "portable.cutvokepack.zip"; pack_project(project, str(package))
        directory = library.directory("portable")
    finally:
        service.close(); library.close()
    first.unlink(); second.unlink()
    imported, mapping, warnings = unpack_project(str(package), str(tmp_path / "unpacked"))
    assert not warnings and len(mapping) == 2
    output = tmp_path / "unpacked.png"; renderer.extract_frame(imported, 3.5, str(output))
    assert np.abs(np.asarray(Image.open(output)).astype(float)-np.asarray(Image.open(expected))).max() < 3
    moved = tmp_path / "moved"; shutil.move(str(directory), str(moved))
    reopened = DirectoryProjectStore(moved)
    try:
        restored = reopened.load("portable")
        assert restored.sequence.tracks[-1].clips[0].sequence_ref == "master"
        assert restored.sequence.tracks[-1].clips[0].property_overrides == {"visibility": .5}
        assert restored.sequence.tracks[0].clips[0].compositing["matte"]["clipId"] == "matte"
        assert restored.sequence.tracks[0].clips[0].property_links
        for seq in restored.sequences:
            for track in seq.tracks:
                for clip in track.clips:
                    if clip.asset_ref.source_path:
                        assert Path(clip.asset_ref.source_path).is_relative_to(moved / "media")
                        assert Path(clip.asset_ref.source_path).is_file()
        output = tmp_path / "moved.png"; renderer.extract_frame(restored, 3.5, str(output))
        assert np.abs(np.asarray(Image.open(output)).astype(float)-np.asarray(Image.open(expected))).max() < 3
    finally:
        reopened.close()


def test_textured_embedded_mesh_survives_package_and_directory_move(tmp_path):
    image=tmp_path/'material.png';Image.new('RGB',(16,16),'#44bb77').save(image)
    library=ProjectLibraryStore(str(tmp_path/'library.sqlite'));service=EditService(library)
    renderer=RenderService(gpu_effects=False);project=service.create_project('mesh',width=96,height=64,fps=R.of(10))
    def command(kind,**payload):return service.execute(Command(kind,payload,project_id='mesh',expected_revision=service.get_project('mesh').revision))
    try:
        command('composition.scene3d',clipId='scene',timelineEnd=1)
        instance=service.get_project('mesh').sequence.tracks[0].clips[0].effects[0]['instanceId']
        command('effect.update',clipId='scene',instanceId=instance,params={'renderer':'software','objects':[{'id':'textured','shape':'plane','color':'#ffffff','texturePath':str(image)}],'camera':{'z':3},'light':{'ambient':1,'intensity':0}},replaceParams=True)
        command('scene3d.import',clipId='scene',instanceId=instance,objectId='textured',objText='v -1 -1 0\nv 1 -1 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt .5 1\nf 1/1 2/2 3/3')
        project=service.get_project('mesh');directory=library.directory('mesh')
        params=project.sequence.tracks[0].clips[0].effects[0]['params'];texture=params['objects'][0]['texturePath']
        assert Path(texture).is_relative_to(directory/'media') and Path(texture).is_file()
        expected=tmp_path/'expected.png';renderer.extract_frame(project,.5,str(expected))
        package=tmp_path/'mesh.zip';pack_project(project,str(package))
    finally:service.close();library.close()
    image.unlink();moved=tmp_path/'moved-mesh';shutil.move(str(directory),str(moved))
    unpacked,mapping,warnings=unpack_project(str(package),str(tmp_path/'unpacked-mesh'))
    assert not warnings and len(mapping)==1
    assert Path(unpacked.sequence.tracks[0].clips[0].effects[0]['params']['objects'][0]['texturePath']).is_relative_to(tmp_path/'unpacked-mesh')
    result=tmp_path/'unpacked.png';renderer.extract_frame(unpacked,.5,str(result))
    assert np.array_equal(np.asarray(Image.open(expected)),np.asarray(Image.open(result)))
    store=DirectoryProjectStore(moved)
    try:
        restored=store.load('mesh');obj=restored.sequence.tracks[0].clips[0].effects[0]['params']['objects'][0]
        assert obj['shape']=='mesh' and Path(obj['texturePath']).is_relative_to(moved/'media')
        result=tmp_path/'moved.png';renderer.extract_frame(restored,.5,str(result))
        assert np.array_equal(np.asarray(Image.open(expected)),np.asarray(Image.open(result)))
    finally:store.close()
