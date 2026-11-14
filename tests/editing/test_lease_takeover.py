"""Lease fencing at admission and SQLite's final write boundary."""
from unittest.mock import patch
import pytest
from cutvoke.domain.protocol import Actor, Command
from cutvoke.editing.service import EditError, EditService
from cutvoke.storage.store import ProjectStore


def edit(service, token="", cid="write"):
    project = service.get_project("p")
    return Command(type="track.add", payload={"trackId": cid, "kind": "video"},
                   command_id=cid, project_id="p", expected_revision=project.revision,
                   actor=Actor("agent" if token else "human", "test"), edit_lease_id=token)


@pytest.fixture
def peers(tmp_path):
    stores = [ProjectStore(str(tmp_path / "project.sqlite")) for _ in range(2)]
    services = [EditService(store=s) for s in stores]
    services[0].create_project("p")
    yield services, stores
    for store in stores:
        store.close()


@pytest.mark.parametrize("persistent", [False, True])
def test_released_token_cannot_write_even_without_replacement(peers, persistent):
    service = peers[0][0] if persistent else EditService()
    if not persistent:
        service.create_project("p")
    token = service.acquire_edit_lease("p")["leaseId"]
    service.release_edit_lease("p", token)
    with pytest.raises(EditError, match="[Rr]ead.*project"):
        service.execute(edit(service, token))
    assert service.get_project("p").revision == "0"
    assert service.execute(edit(service, cid="human")).revision == "1"


@pytest.mark.parametrize("operation", ["command", "srt", "rename"])
def test_late_commit_is_fenced_across_connections(peers, operation):
    (agent, human), (store, _) = peers
    token = agent.acquire_edit_lease("p")["leaseId"]
    method = "rename_project" if operation == "rename" else "commit"
    original = getattr(store, method)
    original_depth = store.history_depth("p")

    def revoke_then_commit(*args, **kwargs):
        assert human.release_edit_lease("p", token)
        return original(*args, **kwargs)

    with patch.object(store, method, side_effect=revoke_then_commit):
        with pytest.raises(EditError, match="[Rr]ead.*project"):
            if operation == "command":
                agent.execute(edit(agent, token))
            elif operation == "srt":
                agent.import_srt("p", "1\n00:00:00,000 --> 00:00:01,000\nlate\n", edit_lease_id=token)
            else:
                agent.rename_project("p", "late name", edit_lease_id=token)
    fresh = human.get_project("p")
    assert fresh.revision == "0"
    assert fresh.name != "late name"
    assert fresh.sequence.tracks == [] and fresh.sequence.captions == []
    assert store.events_since("p") == []
    assert store.history_depth("p") == original_depth


@pytest.mark.parametrize("persistent", [False, True])
def test_takeover_preserves_project_records_human_and_rejects_replays(peers, persistent):
    service = peers[0][0] if persistent else EditService()
    if not persistent:
        service.create_project("p")
    lease = service.acquire_edit_lease("p", owner="caption-agent")
    committed = edit(service, lease["leaseId"], "already-saved")
    receipt = service.execute(committed)
    before = service.get_project("p").to_dict()
    result = service.takeover_edit_lease("p", lease["leaseId"], actor=Actor("human", "web-editor"))
    assert result == dict(status="taken-over", takenOver=True, locked=False, lease=None)
    assert service.get_project("p").to_dict() == before
    # A legal committed replay is a receipt, not a new write.
    assert service.execute(committed).to_dict() == receipt.to_dict()
    for operation in [lambda: service.execute(edit(service, lease["leaseId"])),
                      lambda: service.renew_edit_lease("p", lease["leaseId"]),
                      lambda: service.acquire_edit_lease("p", lease_id=lease["leaseId"])]:
        with pytest.raises(EditError) as error:
            operation()
        assert error.value.code == "EDIT_LEASE_INVALID"
        assert not error.value.retryable
        assert "Read the latest project" in error.value.message
    events = [e.to_dict() for e in service.events_since("p", "0")]
    audit = [e for e in events if e["type"] == "edit.lease.takenOver"]
    assert len(audit) == 1 and audit[0]["actor"] == {"kind": "human", "id": "web-editor"}
    assert audit[0]["revision"] == audit[0]["previousRevision"] == receipt.revision
    assert audit[0]["changedEntities"][0]["owner"] == "caption-agent"
    assert audit[0]["changedEntities"][0]["at"] > 0
    assert not service.takeover_edit_lease("p", lease["leaseId"], actor=Actor("human", "web-editor"))["takenOver"]
    assert len([e for e in service.events_since("p", "0") if e.type == "edit.lease.takenOver"]) == 1
    assert service.execute(edit(service, cid="human")).revision == "2"


def test_stale_takeover_cannot_revoke_replacement_and_expired_tokens_stay_invalid(peers):
    (agent, human), (store, _) = peers
    old = agent.acquire_edit_lease("p", owner="old")["leaseId"]
    store._conn.execute("UPDATE edit_leases SET expires_at=0 WHERE project_id='p'")
    assert human.get_edit_lease("p") is None
    with pytest.raises(EditError):
        agent.execute(edit(agent, old))
    with pytest.raises(EditError):
        agent.acquire_edit_lease("p", lease_id=old)
    new = agent.acquire_edit_lease("p", owner="replacement")
    result = human.takeover_edit_lease("p", old, actor=Actor("human", "web-editor"))
    assert result["status"] == "lease-changed" and result["lease"] == new
    assert human.get_edit_lease("p") == new and store.events_since("p") == []
    with pytest.raises(EditError) as error:
        human.execute(edit(human, cid="blocked-human"))
    assert error.value.code == "EDIT_LOCKED"
    assert agent.execute(edit(agent, new["leaseId"], "new-agent")).revision == "1"


def test_takeover_audit_failure_rolls_back_revocation(peers):
    (agent, human), (_, store) = peers
    lease = agent.acquire_edit_lease("p")
    with patch.object(store, "_next_event_seq", side_effect=OSError("audit unavailable")):
        with pytest.raises(OSError):
            human.takeover_edit_lease("p", lease["leaseId"], actor=Actor("human", "web-editor"))
    assert human.get_edit_lease("p") == lease
    assert store.events_since("p") == []


def test_two_connections_only_take_over_once_and_audit_revision_zero(peers):
    from concurrent.futures import ThreadPoolExecutor
    (agent, human), (store, _) = peers
    token = agent.acquire_edit_lease("p")["leaseId"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda service: service.takeover_edit_lease(
            "p", token, actor=Actor("human", "web-editor")), [agent, human]))
    assert sum(result["takenOver"] for result in outcomes) == 1
    assert len(store.events_since("p")) == 1
    assert len(agent.events_since("p", "0")) == 1
    # Restart cannot resurrect a revoked lease ID.
    restarted = EditService(store=store)
    with pytest.raises(EditError):
        restarted.acquire_edit_lease("p", lease_id=token)


@pytest.mark.parametrize("operation", ["command", "srt", "rename"])
def test_takeover_fences_an_admitted_request_without_advancing_revision(peers, operation):
    (agent, human), (store, _) = peers
    token = agent.acquire_edit_lease("p")["leaseId"]
    method = "rename_project" if operation == "rename" else "commit"
    original = getattr(store, method)
    def takeover_before_write(*args, **kwargs):
        assert human.takeover_edit_lease("p", token, actor=Actor("human", "web-editor"))["takenOver"]
        return original(*args, **kwargs)
    with patch.object(store, method, side_effect=takeover_before_write):
        with pytest.raises(EditError) as error:
            if operation == "command":
                agent.execute(edit(agent, token))
            elif operation == "srt":
                agent.import_srt("p", "1\n00:00:00,000 --> 00:00:01,000\nlate\n", edit_lease_id=token)
            else:
                agent.rename_project("p", "late", edit_lease_id=token)
    assert error.value.code == "EDIT_LEASE_INVALID"
    project = human.get_project("p")
    assert project.revision == "0" and project.name != "late"
    assert not project.sequence.tracks and not project.sequence.captions
    assert len(store.events_since("p")) == 1
    assert store.get_idempotent("p", "write") is None


def test_directory_library_routes_takeover_and_fences_child_commit(tmp_path):
    from cutvoke.storage.project_directory import ProjectLibraryStore
    stores = [ProjectLibraryStore(str(tmp_path / "library.sqlite")) for _ in range(2)]
    try:
        agent, human = [EditService(store=s) for s in stores]
        agent.create_project("p")
        lease = agent.acquire_edit_lease("p")
        original = stores[0].commit
        def takeover_then_commit(*args, **kwargs):
            assert human.takeover_edit_lease("p", lease["leaseId"], actor=Actor("human", "web-editor"))["takenOver"]
            return original(*args, **kwargs)
        with patch.object(stores[0], "commit", side_effect=takeover_then_commit):
            with pytest.raises(EditError) as error:
                agent.execute(edit(agent, lease["leaseId"]))
        assert error.value.code == "EDIT_LEASE_INVALID"
        assert human.get_project("p").revision == "0"
        assert len(human.events_since("p", "0")) == 1
        assert stores[0]._conn.execute("SELECT COUNT(*) FROM edit_leases").fetchone()[0] == 0
        assert human.execute(edit(human, cid="human")).revision == "1"
    finally:
        for store in stores:
            store.close()


def test_http_takeover_records_editor_identity_and_validates_observed_token(peers, tmp_path):
    from cutvoke.interfaces.httpapi import HttpApi
    from cutvoke.rendering.render import RenderService
    (agent, human), _ = peers
    lease = agent.acquire_edit_lease("p", owner="http-agent")
    api = HttpApi(human, RenderService(), media_dir=str(tmp_path / "media"))
    try:
        route = "/api/v1/projects/p/edit-lock/takeover"
        status, result = api.handle("POST", route, {})
        assert status == 400 and result["error"]["code"] == "INVALID_ARGUMENT"
        assert human.get_edit_lease("p") == lease
        status, result = api.handle("POST", route, {"leaseId": "stale"})
        assert status == 200 and result["status"] == "lease-changed"
        assert human.get_edit_lease("p") == lease
        status, result = api.handle("POST", route, {"leaseId": lease["leaseId"], "actor": {"kind": "agent", "id": "spoof"}})
        assert status == 200 and result["takenOver"]
        event = human.events_since("p", "0")[0].to_dict()
        assert event["actor"] == {"kind": "human", "id": "web-editor"}
        assert event["revision"] == "0"
        status, result = api.handle("POST", "/api/v1/projects/p/edit-lock/renew", {"leaseId": lease["leaseId"]})
        assert status == 409 and result["error"]["code"] == "EDIT_LEASE_INVALID"
    finally:
        api.close()


def test_mcp_invalid_lease_recovery_requires_reading_and_human_handoff(peers, tmp_path):
    from cutvoke.interfaces.mcp_server import MCPServer
    (agent, human), _ = peers
    mcp = MCPServer(agent, media_dir=str(tmp_path / "mcp-media"))
    token = agent.acquire_edit_lease("p")["leaseId"]
    human.takeover_edit_lease("p", token, actor=Actor("human", "web-editor"))
    error = mcp.call_tool("edit_lock", {"projectId": "p", "action": "renew", "leaseId": token})["error"]
    assert error["code"] == "EDIT_LEASE_INVALID" and not error["retryable"]
    assert "Read the latest project" in error["recovery"]["message"]
    assert "human handoff" in error["recovery"]["message"]
    assert "never automatically reclaim" in error["recovery"]["message"]


def test_a_lease_acquired_during_a_human_command_fences_the_final_write(peers):
    (human, agent), (store, _) = peers
    original = store.commit
    def acquire_before_commit(*args, **kwargs):
        agent.acquire_edit_lease("p", owner="new-agent")
        return original(*args, **kwargs)
    with patch.object(store, "commit", side_effect=acquire_before_commit):
        with pytest.raises(EditError) as error:
            human.execute(edit(human, cid="human"))
    assert error.value.code == "EDIT_LOCKED"
    assert human.get_project("p").revision == "0"
    assert store.get_idempotent("p", "human") is None and not store.events_since("p")


def test_workspace_maintenance_cannot_bypass_a_late_token_revocation(peers, tmp_path):
    import hashlib
    (agent, human), (store, _) = peers
    source, target = tmp_path / "before.png", tmp_path / "after.png"
    source.write_bytes(b"same-media"); target.write_bytes(source.read_bytes())
    human.execute(Command(type="clip.insert", payload={
        "trackId": "video", "createTrackKind": "video", "clipId": "source-clip",
        "sourcePath": str(source), "timelineStart": {"num": 0, "den": 1},
        "timelineEnd": {"num": 1, "den": 1}}, command_id="seed", project_id="p",
        expected_revision="0", actor=Actor("human", "test")))
    token = agent.acquire_edit_lease("p")["leaseId"]
    original = store.relocate_paths
    def revoke_then_relocate(*args, **kwargs):
        assert human.takeover_edit_lease("p", token, actor=Actor("human", "web-editor"))["takenOver"]
        return original(*args, **kwargs)
    with patch.object(store, "relocate_paths", side_effect=revoke_then_relocate):
        with pytest.raises(EditError) as error:
            agent.execute(Command(type="workspace.relocatePaths", payload={
                "moves": [{"from": str(source), "to": str(target),
                           "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}],
                "expectedProjects": {"p": "1"}}, command_id="relocate", project_id="p",
                expected_revision="1", actor=Actor("agent", "test"), edit_lease_id=token))
    assert error.value.code == "EDIT_LEASE_INVALID"
    project = human.get_project("p")
    assert project.revision == "1"
    assert project.sequence.tracks[0].clips[0].asset_ref.source_path == str(source)
    assert store.get_idempotent("__workspace__", "relocate") is None


def test_committed_workspace_receipt_stays_replayable_after_takeover(peers, tmp_path):
    import hashlib
    (agent, human), (store, _) = peers
    source, target = tmp_path / "from.png", tmp_path / "to.png"
    source.write_bytes(b"same-media"); target.write_bytes(source.read_bytes())
    command = Command(type="workspace.relocatePaths", payload={
        "moves": [{"from": str(source), "to": str(target),
                   "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}],
        "expectedProjects": {"p": "0"}}, command_id="maintenance", project_id="p",
        expected_revision="0", actor=Actor("human", "test"))
    receipt = human.execute(command)
    token = agent.acquire_edit_lease("p")["leaseId"]
    human.takeover_edit_lease("p", token, actor=Actor("human", "web-editor"))
    command.edit_lease_id = token
    before = store.get_idempotent("__workspace__", "maintenance")
    assert agent.execute(command).to_dict() == receipt.to_dict()
    assert store.get_idempotent("__workspace__", "maintenance") == before
