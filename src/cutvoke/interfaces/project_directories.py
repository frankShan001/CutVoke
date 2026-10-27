"""HTTP routing and maintenance endpoints for independent project folders."""
from __future__ import annotations

from pathlib import Path

from cutvoke.storage.project_directory import DirectoryProjectStore, ProjectLibraryStore, ENTRY
from cutvoke.storage.project_directory_migration import plan_migration, migrate_project, rollback_migration


def directory_endpoint(api, method, parts, body):
    store = api.service.store
    if method == "POST" and parts == ["api", "v1", "project-directories", "open"]:
        if not isinstance(store, ProjectLibraryStore):
            raise ValueError("请从工程列表打开独立工程目录")
        project = store.open_directory(body.get("path", ""))
        previous = api._directory_apis.pop(project.project_id, None)
        if previous:
            previous.close()
        return 200, {"projectId": project.project_id, "name": project.name,
                     "directory": str(store.directory(project.project_id))}
    if len(parts) < 5 or parts[:3] != ["api", "v1", "projects"] or parts[4] != "directory":
        return None
    pid = parts[3]
    if method == "GET" and len(parts) == 5:
        root = store.directory(pid) if isinstance(store, ProjectLibraryStore) else getattr(store, "root", None)
        if root:
            return 200, {"projectId": pid, "storageKind": "directory", "directory": str(root),
                         "entryPath": str(root / ENTRY), "mediaDirectory": str(root / "media"),
                         "cacheDirectory": str(root / "cache"), "exportDirectory": str(root / "exports"),
                         "backupDirectory": str(root / "backups")}
        api.service.get_project(pid)
        return 200, {"projectId": pid, "storageKind": "legacy", "directory": None}
    if not isinstance(store, ProjectLibraryStore):
        raise ValueError("请从工程库管理旧工程迁移")
    if len(parts) == 6 and method == "POST":
        if parts[5] == "plan":
            return 200, plan_migration(store, pid)
        if parts[5] in ("migrate", "rollback"):
            # Preview readers can finish against the retained original files.
            # Unlike whole-library relocation, copying a single project does not
            # require all preview/background activity in the app to stop.
            for active in [api, *list(api._directory_apis.values())]:
                manager = active._person_cutout_jobs
                with manager._lock:
                    if any(thread.is_alive() for thread in manager._threads.values()):
                        raise ValueError("请先完成人物抠像任务，再迁移工程")
            previous = api._directory_apis.pop(pid, None)
            if previous:
                previous.close()
            if parts[5] == "rollback":
                return 200, rollback_migration(store, pid)
            return 200, migrate_project(store, pid, token=body.get("token"), directory=body.get("directory"))
    return None


def route_project(api, method, parts, body):
    if not isinstance(api.service.store, ProjectLibraryStore):
        return api
    pid = None
    if parts[:3] == ["api", "v1", "projects"] and len(parts) >= 4:
        # Clone creates a NEW project in the library, not in the source folder.
        if len(parts) >= 5 and parts[4] == "clone":
            return api
        pid = parts[3]
    elif parts[:3] in (["api", "v1", "assets"], ["api", "v1", "luts"], ["api", "v1", "preview-cache"]):
        pid = body.get("projectId")
        if not pid and len(parts) >= 4 and parts[2] == "assets":
            shared = api.service.store.get_asset(parts[3])
            if not shared or not shared.get("builtin"):
                pid = api.service.store.owner_of_asset(parts[3])
    elif len(parts) >= 4 and parts[:3] == ["api", "v1", "jobs"]:
        queue = api.service._export_queue
        if queue and parts[3] in queue._jobs:
            return api
        job = api.service.store.get_export_job(parts[3])
        pid = job.get("projectId") if job else None
    elif len(parts) >= 4 and parts[:2] == ["api", "v1"] and parts[2] in ("preview-jobs", "asr-jobs", "person-cutout-jobs"):
        for child in list(api._directory_apis.values()):
            manager = (child._preview_preparation if parts[2] == "preview-jobs" else
                       child._asr_jobs if parts[2] == "asr-jobs" else child._person_cutout_jobs)
            try:
                if manager.get(parts[3]):
                    return child
            except KeyError:
                pass
        if parts[2] in {"asr-jobs","person-cutout-jobs"}:
            for project in api.service.store.list_projects():
                if project.get("storageKind") == "directory" and project.get("available", True):
                    child = api.project_api(project["id"])
                    try:
                        manager=child._asr_jobs if parts[2]=='asr-jobs' else child._person_cutout_jobs
                        if manager.get(parts[3]):
                            return child
                    except KeyError:
                        pass
    return api.project_api(pid) if pid else api
