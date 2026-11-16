"""Creative routing/contracts via MCP and HTTP; no aesthetic score or external media."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from cutvoke.editing.service import EditService
from cutvoke.interfaces.creative_workflow import CreativeWorkflow, _catalog
from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.storage.store import ProjectStore


def rat(n):
    return {'num': str(n), 'den': '1'}


def span(a, b):
    return {'start': rat(a), 'end': rat(b)}


def artifacts(project, asset):
    base = dict(schemaVersion=1, projectId=project.project_id, revision=project.revision,
                sequenceId=project.sequence.id)
    plan = dict(base, kind='plan', recipeId='product-demonstration',
        brief=dict(purpose='product-brand', audience='A first-time user', takeaway='A true result', successCriterion='Explain the task and condition'),
        delivery=dict(width=320, height=180, viewingContext='Landscape, sound on; also inspect small text'),
        assetRoles=[dict(id='source-a',assetId=asset, role='Synthetic demonstration, not a real product claim',sourceRange=span(0,4),provenance='illustration',rightsNote='Original test image')],
        protectedRanges=[dict(clipId='keep', range=span(4,8), reason='Required result and limitation')],
        beats=[dict(id='task',range=span(0,4),clipIds=['main'],selectionIds=['source-a'],intent='Introduce task',audioIntent='Synthetic tone, not speech',captionIntent='True condition'),
               dict(id='result',range=span(4,8),clipIds=['keep'],selectionIds=['source-a'],intent='Retain outcome',audioIntent='Keep tone',captionIntent='Required condition')],
        gaps=[], intentionalExceptions=[], reviewRequirements=['technical','continuous-av'])
    review = dict(base,kind='review',evidence=[dict(id='e1',kind='technical',range=span(0,4),artifact='notes/caption-comparison.json',inspection='inspected',inspector='deterministic fixture assertion',observation='Caption differs from the specified literal condition; no sensory judgment')],issues=[dict(id='i1',range=span(0,4),severity='major',observation='Wrong qualifier in fixture caption',evidenceIds=['e1'],suggestedAction='Correct caption only',preserve=['Keep timing, media and result'],status='open')],unassessed=['Full continuous AV and audience comprehension'])
    repair = dict(base,kind='repair',issueIds=['i1'],scope=span(0,4),commands=[dict(type='caption.update',payload=dict(captionId='condition',text='Demo condition'))],preserveClipIds=['main','keep'],rationale='Correct the fixture literal without retiming',recheckRanges=[span(0,8)],recheckRequirements=['technical','continuous-av'])
    return plan,review,repair


@pytest.fixture
def workspace(tmp_path):
    store=ProjectStore(str(tmp_path/'p.sqlite')); service=EditService(store); server=MCPServer(service)
    def call(name, **a):
        result=server.call_tool(name,a)
        assert result['ok'],result
        return result
    call('create_project',projectId='p',width=320,height=180,fps=10)
    image=tmp_path/'source.png';Image.new('RGB',(320,180),(12,100,200)).save(image)
    asset=call('media_import',paths=[str(image)])['assets'][0]['assetId']
    edits=[{'type':'clip.insert','payload':dict(trackId='v',createTrackKind='video',clipId='main',assetId=asset,timelineStart=rat(0),timelineEnd=rat(4))},
           {'type':'clip.insert','payload':dict(trackId='v',clipId='keep',assetId=asset,timelineStart=rat(4),timelineEnd=rat(8))},
           {'type':'caption.add','payload':dict(captionId='condition',text='Wrong condition',start=rat(0),end=rat(4))}]
    call('command_apply',projectId='p',expectedRevision='0',type='edit.batch',payload={'commands':edits},commandId='assemble')
    yield service,server,call,artifacts(service.get_project('p'),asset),tmp_path
    server.close();store.close()


def test_discovery_is_bounded_bilingual_and_does_not_initialize_renderer():
    server=MCPServer(EditService())
    try:
        with patch.object(server,'_agent_tools',side_effect=AssertionError('optional render initialization')):
            result=server.call_tool('creative_workflow',{'operation':'list','query':'教程','locale':'zh-CN'})
            assert [r['id'] for r in result['recipes']]==['task-tutorial']
            assert '可复现' in result['recipes'][0]['title']
            overview=server.call_tool('editor_help',{'topic':'creative'})['help']
            assert overview['recipes']['tool']=='creative_workflow'
            seen=[]; offset=0
            while True:
                page=server.call_tool('creative_workflow',{'operation':'list','offset':offset})
                assert len(page['recipes'])<=3 and 'structure' not in page['recipes'][0]
                assert len(json.dumps(page,ensure_ascii=False).encode())<3500
                seen.extend(r['id'] for r in page['recipes'])
                if page['nextOffset'] is None:break
                offset=page['nextOffset']
            assert len(seen)==len(set(seen))==18
            for key in seen:
                for locale in ('en','zh-CN'):
                    detail=server.call_tool('creative_workflow',{'operation':'recipe','recipeId':key,'locale':locale})
                    assert detail['ok'] and detail['recipe']['sources']
                    for field in ('materialPrerequisites','structure','pictureRhythm','audio','captions','failureModes','acceptance','fallback'):
                        assert detail['recipe'][field]
                    assert 'not proven' in detail['recipe']['qualityClaim']
    finally:server.close()


@pytest.mark.parametrize('a',[
 {'operation':'list','limit':True},{'operation':'list','limit':11},{'operation':'list','offset':-1},
 {'operation':'list','offset':10**400},{'operation':'list','limit':10**400},
 {'operation':'list','purpose':'../unknown'},{'operation':'list','style':'cinematic'},
 {'operation':'list','query':'x'*121},{'operation':'recipe','recipeId':'../../private'},
 {'operation':'list','recipeId':'task-tutorial'},{'operation':'schema'},{'operation':'validate'},
 {'operation':'recipe','locale':'de'},{'operation':'schema','kind':'plan','extra':True}])
def test_invalid_arguments_match_mcp_http(workspace,a):
    service,server,_,_,root=workspace
    api=HttpApi(service,media_dir=str(root/'media'))
    try:
        mcp=server.call_tool('creative_workflow',a)
        status,http=api.handle('POST','/api/v1/agent/creative_workflow',a)
        assert not mcp['ok'] and status==400,(mcp,http)
        assert http['error']['code']=='INVALID_ARGUMENT'
    finally:api.close()


def test_artifact_roundtrip_pure_preview_and_http_parity(workspace):
    service,server,call,(plan,review,repair),root=workspace
    before=copy.deepcopy(service.get_project('p').to_dict())
    for document in (plan,review):
        result=call('creative_workflow',operation='validate',document=document)
        assert result['contractValid'] and result['creativeAcceptance']=='not_assessed'
    a=dict(operation='preview-repair',document=repair,plan=plan,review=review)
    result=call('creative_workflow',**a)
    assert result['readyForCommandApply'],result
    api=HttpApi(service,media_dir=str(root/'media'))
    try:
        status,http=api.handle('POST','/api/v1/agent/creative_workflow',a)
        assert status==200 and http==result
    finally:api.close()
    assert service.get_project('p').to_dict()==before
    args=result['nextCall']['arguments']
    lease=call('edit_lock',projectId='p',action='acquire',owner='creative-test')['lease']['leaseId']
    applied=call('command_apply',**args,editLeaseId=lease,commandId='repair-one')
    replay=call('command_apply',**args,editLeaseId=lease,commandId='repair-one')
    assert replay['revision']==applied['revision']
    call('edit_lock',projectId='p',action='release',leaseId=lease)
    stale=call('creative_workflow',**a)
    assert not stale['contractValid'] and 'nextCall' not in stale
    assert any(f['code']=='STALE_REVISION' for f in stale['findings'])
    undo=call('command_apply',projectId='p',expectedRevision=applied['revision'],type='history.undo',payload={},commandId='undo-repair')
    assert service.get_project('p').sequence.captions[0].text=='Wrong condition'
    assert undo['revision']!=applied['revision']


@pytest.mark.parametrize('change,code',[
 ('wrong-revision','STALE_REVISION'),('other-project','CONTEXT_MISMATCH'),('other-sequence','SEQUENCE_MISMATCH'),
 ('unseen','UNINSPECTED_EVIDENCE'),('outside-scope','OUTSIDE_REPAIR_SCOPE'),('protected','PROTECTED_RANGE'),
 ('preserve','PRESERVE_CHANGED'),('resolved','REPAIR_ISSUE'),('recheck','RECHECK_COVERAGE'),('requirements','RECHECK_REQUIREMENTS'),
 ('unknown-evidence','ISSUE_EVIDENCE'),('unknown-selection','UNKNOWN_SELECTION'),('unknown-clip','UNKNOWN_CLIP')])
def test_repair_refuses_stale_uninspected_and_out_of_scope(workspace,change,code):
    service,_,call,(plan,review,repair),_=workspace
    before=service.get_project('p').to_dict()
    if change=='wrong-revision':repair['revision']='0'
    if change=='other-project':plan['projectId']='other'
    if change=='other-sequence':review['sequenceId']='other'
    if change=='unseen':review['evidence'][0]['inspection']='pending'
    if change=='outside-scope':repair['commands'][0]['payload']['end']=rat(5)
    if change=='protected':plan['protectedRanges'].append(dict(clipId='main',range=span(0,4),reason='Protect claim'))
    if change=='preserve':repair['commands']=[dict(type='clip.audio',payload=dict(clipId='main',volume=0.1))]
    if change=='resolved':review['issues'][0]['status']='resolved'
    if change=='recheck':repair['recheckRanges']=[span(1,8)]
    if change=='requirements':repair['recheckRequirements']=['technical']
    if change=='unknown-evidence':review['issues'][0]['evidenceIds']=['missing']
    if change=='unknown-selection':plan['beats'][0]['selectionIds']=['missing']
    if change=='unknown-clip':plan['beats'][0]['clipIds']=['missing']
    result=call('creative_workflow',operation='preview-repair',document=repair,plan=plan,review=review)
    assert not result['contractValid'] and code in [f['code'] for f in result['findings']],result
    assert 'nextCall' not in result and service.get_project('p').to_dict()==before


@pytest.mark.parametrize('change',['zero-den','reverse-range','float-time','duplicate','too-many','nested','huge','boolean-version'])
def test_malformed_contracts_never_mutate(workspace,change):
    service,server,_,(plan,_,_),_=workspace
    before=service.get_project('p').to_dict()
    if change=='zero-den':plan['beats'][0]['range']['start']['den']='0'
    if change=='reverse-range':plan['beats'][0]['range']=span(4,0)
    if change=='float-time':plan['beats'][0]['range']['start']=0.5
    if change=='duplicate':plan['beats'].append(copy.deepcopy(plan['beats'][0]))
    if change=='too-many':plan['gaps']=['g']*65
    if change=='nested':
        node=plan
        for _ in range(22):node['unknown']={};node=node['unknown']
    if change=='huge':plan['brief']['audience']='x'*2001
    if change=='boolean-version':plan['schemaVersion']=True
    result=server.call_tool('creative_workflow',dict(operation='validate',document=plan))
    assert not result['ok'],result
    assert service.get_project('p').to_dict()==before


def test_locked_track_and_side_effect_commands_rejected(workspace):
    service,server,call,(plan,review,repair),_=workspace
    for commands in ([dict(type='export.enqueue',payload={'outPath':'/tmp/should-not-exist.mp4'})],
                     [dict(type='edit.batch',payload={'commands':repair['commands']})]):
        repair['commands']=commands
        result=server.call_tool('creative_workflow',dict(operation='preview-repair',document=repair,plan=plan,review=review))
        assert not result['ok'],result
    # Exact payload help remains authoritative for commands in a repair.
    schemas=call('creative_workflow',operation='schema',kind='repair')['schema']
    assert schemas['properties']['commands']['maxItems']==32


def test_candidate_diff_catches_underreported_impact(workspace):
    service,_,call,(plan,review,repair),_=workspace
    original=service.preview_command
    def hidden(command,**kw):
        value=original(command,**kw)
        keep=value['project'].sequence.tracks[0].clips[1]
        keep.volume=type(keep.volume).of(1,10)
        value['changedEntities']=[]  # Handler metadata cannot authorize unreported changes.
        return value
    with patch.object(service,'preview_command',side_effect=hidden):
        result=call('creative_workflow',operation='preview-repair',document=repair,plan=plan,review=review)
    assert not result['readyForCommandApply']
    codes={f['code'] for f in result['findings']}
    assert {'PRESERVE_CHANGED','PROTECTED_RANGE','OUTSIDE_REPAIR_SCOPE'}<=codes


def test_nested_shared_and_property_dependencies_are_conservative(workspace):
    from cutvoke.domain.controllers import resolve
    from cutvoke.domain.keyframes import evaluate
    from cutvoke.domain.rational import Rational
    service,server,call,(plan,review,repair),_=workspace
    # The real resolver changes a keep clip despite identical serialized state.
    project=service.get_project('p'); main,keep=project.sequence.tracks[0].clips
    keep.property_links={'opacity':{'source':{'type':'property','clipId':'main','param':'opacity'}}}
    service.store.save(project)
    before=copy.deepcopy(service.get_project('p'))
    commands=[dict(type='clip.keyframe',payload=dict(clipId='main',action='add',param='opacity',time=rat(0),value=.2))]
    candidate=service.preview_command(__import__('cutvoke.domain.protocol',fromlist=['Command']).Command(type='edit.batch',payload={'commands':commands},project_id='p',expected_revision=project.revision),include_project=True)['project']
    assert candidate.sequence.tracks[0].clips[1].to_dict()==before.sequence.tracks[0].clips[1].to_dict()
    resolved=resolve(candidate).sequence.tracks[0].clips[1]
    assert evaluate(resolved.keyframes['opacity'],Rational.of(2))==pytest.approx(.2)
    repair['commands']=commands;repair['preserveClipIds']=['keep']
    result=call('creative_workflow',operation='preview-repair',document=repair,plan=plan,review=review)
    assert not result['readyForCommandApply'] and any(f['code']=='DEPENDENCY_REQUIRES_CONTEXT' for f in result['findings'])
    repair['commands']=[dict(type='composition.edit',payload=dict(path=['holder'],command=commands[0]))]
    with patch.object(service,'preview_command',side_effect=AssertionError('unsupported nested preview')):
        result=call('creative_workflow',operation='preview-repair',document=repair,plan=plan,review=review)
    assert not result['readyForCommandApply'] and any(f['code']=='COMPOSITION_REQUIRES_CONTEXT' for f in result['findings'])
    assert service.get_project('p').to_dict()==before.to_dict()


def test_directory_asset_authority_and_repeated_selections():
    from types import SimpleNamespace
    from cutvoke.storage.project_directory import ProjectLibraryStore
    parent,child=ProjectStore(':memory:'),ProjectStore(':memory:')
    service=EditService(parent)
    project=service.create_project('p',width=320,height=180)
    try:
        from cutvoke.domain.model import Clip,Track,AssetReference
        from cutvoke.domain.rational import Rational as R
        project.sequence.tracks=[Track('v','video',[Clip('main',AssetReference('used'),R.of(0),R.of(4),R.of(0)),Clip('keep',AssetReference('used'),R.of(4),R.of(8),R.of(0))])]
        parent.save(project)
        for store,key in [(child,'local-unused'),(parent,'foreign-only'),(child,'same-id'),(parent,'same-id')]:
            store.add_asset(asset_id=key,name=key,path='/not-opened',size=0,kind='video',duration=4 if store is child else 100)
        # Exercise the actual project-library route against two isolated stores.
        router=SimpleNamespace(_target=lambda pid:child)
        def lookup(pid,asset_id):return ProjectLibraryStore.get_project_asset(router,pid,asset_id)
        with patch.object(parent,'get_project_asset',side_effect=lookup):
            plan,_,_=artifacts(project,'used')
            plan['assetRoles'].append(dict(plan['assetRoles'][0],id='alternate',assetId='local-unused'))
            plan['assetRoles'].append(dict(plan['assetRoles'][0],id='same-source-second-use'))
            assert CreativeWorkflow(service).call(dict(operation='validate',document=plan))['contractValid']
            plan['assetRoles'][-1].update(assetId='foreign-only')
            result=CreativeWorkflow(service).call(dict(operation='validate',document=plan))
            assert any(f['code']=='UNKNOWN_ASSET' for f in result['findings'])
            plan['assetRoles'][-1].update(assetId='same-id',sourceRange=span(0,5))
            result=CreativeWorkflow(service).call(dict(operation='validate',document=plan))
            assert any(f['code']=='SOURCE_RANGE' for f in result['findings']) # child duration 4, not parent's 100
    finally:service.close();parent.close();child.close()


@pytest.mark.parametrize('dependency',['master','track','bus','sidechain'])
def test_audio_dependencies_block_clip_repairs_but_allow_caption_repairs(workspace,dependency):
    service,_,call,(plan,review,repair),_=workspace
    project=service.get_project('p')
    mix={'effects':[{'effectId':'cutvoke.fx.loudnorm','params':{}}]}
    if dependency=='master':project.sequence.master_audio=mix
    if dependency=='track':project.sequence.tracks[0].audio_mix=mix
    if dependency=='bus':project.sequence.audio_buses=[{'id':'b','name':'test bus','mix':mix}]
    if dependency=='sidechain':
        params=service._effects.validate_params('cutvoke.fx.sidechainDuck',{'keyClipIds':['main']})
        project.sequence.tracks[0].clips[1].effects.append({'effectId':'cutvoke.fx.sidechainDuck','instanceId':'duck','version':'1.0.0','params':params})
    service.store.save(project)
    # The canonical global processor is retained; caption-only changes cannot
    # alter its input. A clip gain change could alter protected later samples.
    result=call('creative_workflow',operation='preview-repair',document=repair,plan=plan,review=review)
    assert result['readyForCommandApply']
    repair['commands']=[dict(type='clip.audio',payload=dict(clipId='main',volume=.1))]
    repair['preserveClipIds']=['keep']
    result=call('creative_workflow',operation='preview-repair',document=repair,plan=plan,review=review)
    assert not result['readyForCommandApply']
    assert any(f['code']=='AUDIO_DEPENDENCY_REQUIRES_CONTEXT' for f in result['findings'])
    assert 'nextCall' not in result


def test_shared_master_same_child_id_cannot_authorize_local_repair():
    # Reuse the complete, current plan/review/repair contract fixture rather
    # than duplicate fields when the versioned schemas evolve.
    from cutvoke.domain.model import AssetReference, Clip, Sequence, Track
    from cutvoke.domain.protocol import Command
    from cutvoke.domain.rational import Rational as R
    service = EditService()
    project = service.create_project('p', width=320, height=180)

    def clip(identifier, start, end, shared=''):
        # This fixture appends clips after the v2 Project constructor, bypassing
        # normal command/import normalization. Build canonical v2 clips here so
        # the preserve-clip assertion still compares the complete state.
        return Clip(identifier, AssetReference('a', source_path='synthetic.png'),
                    R.of(start), R.of(end), R.of(0), sequence_ref=shared,
                    keyframe_time_domain='signed-local')

    master = Sequence('master', 320, 180, R.of(30), tracks=[
        Track('child-v', 'video', [clip('main', 0, 4)])])
    project.sequences.append(master)
    project.sequence.tracks = [
        Track('v', 'video', [clip('main', 0, 4), clip('keep', 4, 8, 'master')]),
        Track('overlay', 'video', [clip('holder', 0, 4, 'master')]),
    ]
    plan, review, repair = artifacts(project, 'a')
    repair['commands'] = [{
        'type': 'composition.edit',
        'payload': {'path': ['holder'], 'command': {
            'type': 'clip.audio', 'payload': {'clipId': 'main', 'volume': 0.1}}},
    }]
    assert repair['scope'] == {'start': {'num': '0', 'den': '1'},
                               'end': {'num': '4', 'den': '1'}}
    assert repair['preserveClipIds'] == ['main', 'keep']
    before = copy.deepcopy(project.to_dict())

    # Prove the command is a valid ordinary edit and really alters the master.
    # Its raw changedEntities points at the unrelated active clip named main.
    raw = service.preview_command(Command(
        type='edit.batch', payload={'commands': repair['commands']},
        project_id='p', expected_revision='0'), include_project=True)
    candidate = raw['project']
    assert candidate.sequences[1].tracks[0].clips[0].volume == R.of(1, 10)
    assert candidate.sequence.tracks[0].clips[1].sequence_ref == 'master'
    assert candidate.sequence.tracks[0].clips[1].to_dict() == project.sequence.tracks[0].clips[1].to_dict()
    assert raw['changedEntities'][0]['id'] == 'main'
    assert raw['changedEntities'][0]['compositionPath'] == ['holder']
    assert raw['changedEntities'][0]['timelineRange'] == {
        'before': repair['scope'], 'after': repair['scope']}

    result = CreativeWorkflow(service).call({
        'operation': 'preview-repair', 'document': repair,
        'plan': plan, 'review': review,
    })
    assert not result['contractValid'], result
    assert not result['readyForCommandApply'], result
    assert 'nextCall' not in result, result
    assert any(f['code'] == 'COMPOSITION_REQUIRES_CONTEXT' for f in result['findings'])
    assert service.get_project('p').to_dict() == before

