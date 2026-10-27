from PIL import Image

from cutvoke.domain.protocol import Command
from cutvoke.editing.service import EditService
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.storage.project_directory import ProjectLibraryStore


def test_transition_comparison_renders_both_variants_without_changing_project(tmp_path):
    store = ProjectLibraryStore(str(tmp_path/'library.sqlite'))
    service = EditService(store)
    server = MCPServer(service)
    try:
        service.create_project('p', width=160, height=90)
        def command(kind, **payload):
            return service.execute(Command(kind,payload,project_id='p',expected_revision=service.get_project('p').revision))
        for index,color in enumerate(['#306080','#e0a060']):
            path = tmp_path/f'{index}.png'; Image.new('RGB',(160,90),color).save(path)
            command('clip.insert',trackId='v',**({'createTrackKind':'video'} if index == 0 else {}),clipId=f'c{index}',sourcePath=str(path),
                    timelineStart={'num':str(index*2),'den':'1'},timelineEnd={'num':str(index*2+2),'den':'1'})
        command('effect.add',clipId='c1',effectId='cutvoke.transition.lumaReveal',params={
            'duration':.5,'spatialBlur':4,'temporalFrames':3,'dissolveMix':.25})
        before = service.get_project('p').to_dict()
        response = server.call_tool('transition_preview',{'projectId':'p','clipId':'c1',
            'expectedRevision':before['revision'],'width':160,'fractions':[.25,.5,.75]})
        assert response['ok'], response
        assert [variant['kind'] for variant in response['variants']] == ['current','crossfade']
        assert all(len(variant['frames']) == 3 for variant in response['variants'])
        assert len(response['_images']) == 6
        assert service.get_project('p').to_dict() == before
    finally:
        server.close(); store.close()
