"""Hosted DocPull control-plane ASGI MVP.

This module is intentionally separate from ``docpull serve``. The pack server
is a read-only local artifact API; this app is the hosted/project control plane
surface for managed sync jobs, exports, releases, and webhooks.
"""

from __future__ import annotations

import hmac
import json
import shutil
import tempfile
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .project import (
    CONTEXT_TARGETS,
    ProjectError,
    ProjectSource,
    _safe_run_id,
    diff_project,
    export_context_pack,
    init_project,
    release_context_pack,
    review_project_run,
    save_project_config,
    sync_project,
)
from .time_utils import utc_now_iso

ASGIScope = MutableMapping[str, Any]
ASGIMessage = MutableMapping[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]
HostedWorker = Callable[["HostedStore", str, str, str], dict[str, Any]]

HOSTED_SCHEMA_VERSION = 1
MAX_HOSTED_BODY_BYTES = 1_048_576
POSTGRES_SCHEMA_SQL = """
CREATE TABLE orgs (id text PRIMARY KEY, name text NOT NULL, created_at timestamptz NOT NULL);
CREATE TABLE api_keys (id text PRIMARY KEY, org_id text NOT NULL REFERENCES orgs(id), key_hash text NOT NULL);
CREATE TABLE projects (id text PRIMARY KEY, org_id text NOT NULL REFERENCES orgs(id), name text NOT NULL);
CREATE TABLE sources (
    id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(id),
    payload_json jsonb NOT NULL
);
CREATE TABLE jobs (
    id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(id),
    status text NOT NULL
);
CREATE TABLE runs (
    id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(id),
    payload_json jsonb NOT NULL
);
CREATE TABLE diffs (
    id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(id),
    payload_json jsonb NOT NULL
);
CREATE TABLE exports (
    id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(id),
    payload_json jsonb NOT NULL
);
CREATE TABLE releases (
    id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(id),
    payload_json jsonb NOT NULL
);
CREATE TABLE webhooks (
    id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(id),
    payload_json jsonb NOT NULL
);
CREATE TABLE audit_events (id text PRIMARY KEY, org_id text NOT NULL, payload_json jsonb NOT NULL);
"""


class HostedAPIError(RuntimeError):
    """HTTP-aware hosted API error."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class HostedStore:
    """In-process hosted metadata store.

    The API shape mirrors the planned Postgres tables while staying dependency
    free for tests and local development.
    """

    api_keys: dict[str, str] = field(default_factory=dict)
    artifact_root: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "docpull-hosted")
    orgs: dict[str, dict[str, Any]] = field(default_factory=dict)
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    diffs: dict[str, dict[str, Any]] = field(default_factory=dict)
    exports: dict[str, dict[str, Any]] = field(default_factory=dict)
    releases: dict[str, dict[str, Any]] = field(default_factory=dict)
    webhooks: dict[str, dict[str, Any]] = field(default_factory=dict)
    webhook_deliveries: list[dict[str, Any]] = field(default_factory=list)
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    _counter: int = 0

    def __post_init__(self) -> None:
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter:08d}"


class HostedASGIApp:
    def __init__(
        self,
        store: HostedStore | None = None,
        *,
        worker: HostedWorker | None = None,
        auto_run_jobs: bool = True,
    ) -> None:
        self.store = store or HostedStore(api_keys={"dev-token": "org_dev"})
        self.worker = worker or default_sync_worker
        self.auto_run_jobs = auto_run_jobs

    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        if str(scope.get("type") or "") == "lifespan":
            await _handle_lifespan(receive, send)
            return
        if str(scope.get("type") or "") != "http":
            await _json_response(send, 500, {"error": "Unsupported ASGI scope"})
            return
        try:
            parts = _path_parts(scope)
            if not parts or parts[0] != "v1":
                raise HostedAPIError(404, "Not found")
            org_id = self._authenticate(scope)
            _ensure_body_size_header(scope, MAX_HOSTED_BODY_BYTES)
            body = await _read_body(receive, max_bytes=MAX_HOSTED_BODY_BYTES)
            payload, status = self._route(scope, body, org_id, parts)
        except HostedAPIError as err:
            payload, status = {"error": str(err)}, err.status
        except Exception as err:  # noqa: BLE001
            payload, status = {"error": f"Hosted API failed: {err}"}, 500
        await _json_response(send, status, payload)

    def _route(
        self,
        scope: ASGIScope,
        body: bytes,
        org_id: str,
        parts: list[str],
    ) -> tuple[dict[str, Any], int]:
        method = str(scope.get("method") or "GET").upper()
        data = _json_body(body)

        if parts == ["v1", "projects"]:
            if method == "GET":
                return {"projects": self._projects_for_org(org_id)}, 200
            if method == "POST":
                return self._create_project(org_id, data), 201
        if len(parts) >= 3 and parts[:2] == ["v1", "projects"]:
            project_id = parts[2]
            project = self._project_for_org(org_id, project_id)
            if len(parts) == 3:
                if method == "GET":
                    return project, 200
                if method == "PATCH":
                    project.update(_project_update(data))
                    self._audit(org_id, "project.updated", {"project_id": project_id})
                    return project, 200
                if method == "DELETE":
                    project["deleted_at"] = utc_now_iso()
                    self._audit(org_id, "project.deleted", {"project_id": project_id})
                    return {"deleted": True, "project_id": project_id}, 200
            if len(parts) == 4 and parts[3] == "sources" and method == "POST":
                return self._create_source(project, data), 201
            if len(parts) == 5 and parts[3] == "sources":
                return self._source_route(method, project, parts[4], data)
            if len(parts) == 4 and parts[3] == "syncs" and method == "POST":
                return self._create_sync_job(project), 202
            if len(parts) == 4 and parts[3] == "runs" and method == "GET":
                return {"runs": self._runs_for_project(project_id)}, 200
            if len(parts) == 5 and parts[3] == "runs" and method == "GET":
                return self._run_for_project(project_id, parts[4]), 200
            if len(parts) == 5 and parts[3] == "diffs" and parts[4] == "latest" and method == "GET":
                return self._latest_diff(project_id), 200
            if len(parts) == 5 and parts[3:5] == ["exports", "context-pack"] and method == "POST":
                return self._create_export(project, data), 201
            if len(parts) == 4 and parts[3] == "releases" and method == "POST":
                return self._create_release(project, data), 201
            if len(parts) == 4 and parts[3] == "webhooks":
                if method == "GET":
                    return {"webhooks": self._webhooks_for_project(project_id)}, 200
                if method == "POST":
                    return self._create_webhook(project, data), 201
            if len(parts) == 5 and parts[3] == "webhooks" and method == "DELETE":
                return self._delete_webhook(project, parts[4]), 200
        if len(parts) == 3 and parts[:2] == ["v1", "jobs"] and method == "GET":
            return self._job_for_org(org_id, parts[2]), 200
        raise HostedAPIError(404, "Not found")

    def _authenticate(self, scope: ASGIScope) -> str:
        headers = _headers(scope)
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise HostedAPIError(401, "Missing Bearer API key")
        token = auth.removeprefix("Bearer ").strip()
        org_id = self.store.api_keys.get(token)
        if not org_id:
            raise HostedAPIError(401, "Invalid API key")
        self.store.orgs.setdefault(org_id, {"id": org_id, "name": org_id, "created_at": utc_now_iso()})
        return org_id

    def _projects_for_org(self, org_id: str) -> list[dict[str, Any]]:
        return [
            project
            for project in self.store.projects.values()
            if project["org_id"] == org_id and not project.get("deleted_at")
        ]

    def _project_for_org(self, org_id: str, project_id: str) -> dict[str, Any]:
        project = self.store.projects.get(project_id)
        if not project or project["org_id"] != org_id or project.get("deleted_at"):
            raise HostedAPIError(404, "Project not found")
        return project

    def _create_project(self, org_id: str, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name") or "").strip()
        if not name:
            raise HostedAPIError(400, "Project name is required")
        project_id = self.store.next_id("proj")
        project = {
            "schema_version": HOSTED_SCHEMA_VERSION,
            "id": project_id,
            "org_id": org_id,
            "name": name,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "source_count": 0,
        }
        self.store.projects[project_id] = project
        self._audit(org_id, "project.created", {"project_id": project_id})
        return project

    def _create_source(self, project: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        try:
            source = ProjectSource.model_validate(data)
        except Exception as err:  # noqa: BLE001
            raise HostedAPIError(400, f"Invalid source: {err}") from err
        source_id = self.store.next_id("src")
        record = {
            "id": source_id,
            "project_id": project["id"],
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "source": source.model_dump(mode="json"),
            "source_public": _hosted_source_public(source),
        }
        self.store.sources[source_id] = record
        project["source_count"] = len(self._source_records(project["id"]))
        project["updated_at"] = utc_now_iso()
        self._audit(
            project["org_id"],
            "source.created",
            {"project_id": project["id"], "source_id": source_id},
        )
        return dict(record["source_public"]) | {"id": source_id, "project_id": project["id"]}

    def _source_route(
        self,
        method: str,
        project: dict[str, Any],
        source_id: str,
        data: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        record = self._source_record(project["id"], source_id)
        if method == "GET":
            return record["source_public"] | {"id": source_id, "project_id": project["id"]}, 200
        if method == "PATCH":
            source_payload = dict(record["source"])
            source_payload.update(data)
            try:
                source = ProjectSource.model_validate(source_payload)
            except Exception as err:  # noqa: BLE001
                raise HostedAPIError(400, f"Invalid source: {err}") from err
            record["source"] = source.model_dump(mode="json")
            record["source_public"] = _hosted_source_public(source)
            record["updated_at"] = utc_now_iso()
            self._audit(
                project["org_id"],
                "source.updated",
                {"project_id": project["id"], "source_id": source_id},
            )
            return record["source_public"] | {"id": source_id, "project_id": project["id"]}, 200
        if method == "DELETE":
            del self.store.sources[source_id]
            project["source_count"] = len(self._source_records(project["id"]))
            self._audit(
                project["org_id"],
                "source.deleted",
                {"project_id": project["id"], "source_id": source_id},
            )
            return {"deleted": True, "source_id": source_id}, 200
        raise HostedAPIError(405, "Unsupported method")

    def _create_sync_job(self, project: dict[str, Any]) -> dict[str, Any]:
        job_id = self.store.next_id("job")
        job = {
            "id": job_id,
            "org_id": project["org_id"],
            "project_id": project["id"],
            "status": "queued",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
        self.store.jobs[job_id] = job
        if self.auto_run_jobs:
            self._run_job(project["org_id"], project["id"], job_id)
        self._audit(project["org_id"], "job.created", {"project_id": project["id"], "job_id": job_id})
        return job

    def _run_job(self, org_id: str, project_id: str, job_id: str) -> None:
        job = self.store.jobs[job_id]
        job["status"] = "running"
        job["updated_at"] = utc_now_iso()
        try:
            payload = self.worker(self.store, org_id, project_id, job_id)
        except Exception as err:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = str(err)
            job["updated_at"] = utc_now_iso()
            return
        job["status"] = "succeeded"
        job["result"] = payload
        job["updated_at"] = utc_now_iso()
        self._emit_webhook(project_id, "run.completed", payload)

    def _job_for_org(self, org_id: str, job_id: str) -> dict[str, Any]:
        job = self.store.jobs.get(job_id)
        if not job or job["org_id"] != org_id:
            raise HostedAPIError(404, "Job not found")
        return job

    def _runs_for_project(self, project_id: str) -> list[dict[str, Any]]:
        return [run for run in self.store.runs.values() if run["project_id"] == project_id]

    def _run_for_project(self, project_id: str, run_id: str) -> dict[str, Any]:
        run = self.store.runs.get(run_id)
        if not run or run["project_id"] != project_id:
            raise HostedAPIError(404, "Run not found")
        return run

    def _run_artifact_for_project(self, project: dict[str, Any], run_id: str) -> tuple[str, Path]:
        try:
            safe_run_id = _safe_run_id(run_id)
        except ProjectError as err:
            raise HostedAPIError(400, str(err)) from err
        run = self._run_for_project(project["id"], safe_run_id)
        artifact_dir = Path(
            str(run.get("artifact_dir") or self.store.artifact_root / project["id"] / "runs" / safe_run_id)
        ).resolve()
        runs_root = (self.store.artifact_root / project["id"] / "runs").resolve()
        try:
            artifact_dir.relative_to(runs_root)
        except ValueError as err:
            raise HostedAPIError(404, "Run artifact not found") from err
        if not artifact_dir.is_dir():
            raise HostedAPIError(404, "Run artifact not found")
        return safe_run_id, artifact_dir

    def _latest_diff(self, project_id: str) -> dict[str, Any]:
        diffs = [item for item in self.store.diffs.values() if item["project_id"] == project_id]
        if not diffs:
            raise HostedAPIError(404, "No diff found")
        return sorted(diffs, key=lambda item: str(item["generated_at"]))[-1]

    def _create_export(self, project: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        target = str(data.get("target") or "")
        if target not in CONTEXT_TARGETS:
            raise HostedAPIError(400, "Invalid context-pack target")
        run_id = str(data.get("run_id") or project.get("latest_run_id") or "")
        if not run_id:
            raise HostedAPIError(400, "No run available to export")
        safe_run_id, artifact_dir = self._run_artifact_for_project(project, run_id)
        with tempfile.TemporaryDirectory(prefix="docpull-hosted-export-") as tmp:
            workspace = Path(tmp)
            _hydrate_project_workspace(workspace, project, self._source_records(project["id"]))
            run_dest = workspace / ".docpull" / "runs" / safe_run_id
            run_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(artifact_dir, run_dest)
            (workspace / ".docpull" / "latest-run").write_text(safe_run_id + "\n", encoding="utf-8")
            export_payload = export_context_pack(target=target, run_id=safe_run_id, root=workspace)
            export_id = self.store.next_id("exp")
            export_record = {
                "id": export_id,
                "project_id": project["id"],
                "generated_at": utc_now_iso(),
                "payload": export_payload,
            }
            self.store.exports[export_id] = export_record
            return export_record

    def _create_release(self, project: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        target = str(data.get("target") or "cursor")
        run_id = str(data.get("run_id") or project.get("latest_run_id") or "")
        tag = data.get("tag")
        if target not in CONTEXT_TARGETS:
            raise HostedAPIError(400, "Invalid context-pack target")
        if not run_id:
            raise HostedAPIError(400, "No run available to release")
        safe_run_id, artifact_dir = self._run_artifact_for_project(project, run_id)
        with tempfile.TemporaryDirectory(prefix="docpull-hosted-release-") as tmp:
            workspace = Path(tmp)
            _hydrate_project_workspace(workspace, project, self._source_records(project["id"]))
            run_dest = workspace / ".docpull" / "runs" / safe_run_id
            run_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(artifact_dir, run_dest)
            (workspace / ".docpull" / "latest-run").write_text(safe_run_id + "\n", encoding="utf-8")
            release_payload = release_context_pack(target=target, run_id=safe_run_id, tag=tag, root=workspace)
            release_id = str(release_payload["tag"])
            record = {
                "id": release_id,
                "project_id": project["id"],
                "generated_at": utc_now_iso(),
                "payload": release_payload,
            }
            self.store.releases[release_id] = record
            self._emit_webhook(project["id"], "release.created", release_payload)
            return record

    def _create_webhook(self, project: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        url = str(data.get("url") or "")
        if not url.startswith("https://"):
            raise HostedAPIError(400, "Webhook URL must be HTTPS")
        raw_events = data.get("events")
        events = raw_events if isinstance(raw_events, list) else ["run.completed"]
        secret = str(data.get("secret") or self.store.next_id("whsec"))
        webhook_id = self.store.next_id("wh")
        record = {
            "id": webhook_id,
            "project_id": project["id"],
            "url": url,
            "events": [str(item) for item in events],
            "secret": secret,
            "created_at": utc_now_iso(),
        }
        self.store.webhooks[webhook_id] = record
        public = _webhook_public(record)
        return public | {"secret": secret}

    def _delete_webhook(self, project: dict[str, Any], webhook_id: str) -> dict[str, Any]:
        record = self.store.webhooks.get(webhook_id)
        if not record or record["project_id"] != project["id"]:
            raise HostedAPIError(404, "Webhook not found")
        del self.store.webhooks[webhook_id]
        return {"deleted": True, "webhook_id": webhook_id}

    def _webhooks_for_project(self, project_id: str) -> list[dict[str, Any]]:
        return [
            _webhook_public(item) for item in self.store.webhooks.values() if item["project_id"] == project_id
        ]

    def _emit_webhook(self, project_id: str, event: str, payload: dict[str, Any]) -> None:
        body = {"event": event, "project_id": project_id, "payload": payload}
        body_bytes = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
        for webhook in self.store.webhooks.values():
            if webhook["project_id"] != project_id or event not in webhook["events"]:
                continue
            signature = sign_webhook_payload(body_bytes, str(webhook["secret"]))
            self.store.webhook_deliveries.append(
                {
                    "webhook_id": webhook["id"],
                    "event": event,
                    "url": webhook["url"],
                    "signature": signature,
                    "payload": body,
                    "created_at": utc_now_iso(),
                }
            )

    def _source_records(self, project_id: str) -> list[dict[str, Any]]:
        return [item for item in self.store.sources.values() if item["project_id"] == project_id]

    def _source_record(self, project_id: str, source_id: str) -> dict[str, Any]:
        record = self.store.sources.get(source_id)
        if not record or record["project_id"] != project_id:
            raise HostedAPIError(404, "Source not found")
        return record

    def _audit(self, org_id: str, event: str, payload: dict[str, Any]) -> None:
        self.store.audit_events.append(
            {
                "id": self.store.next_id("audit"),
                "org_id": org_id,
                "event": event,
                "payload": payload,
                "created_at": utc_now_iso(),
            }
        )


def create_hosted_app(
    store: HostedStore | None = None,
    *,
    worker: HostedWorker | None = None,
    auto_run_jobs: bool = True,
) -> HostedASGIApp:
    return HostedASGIApp(store=store, worker=worker, auto_run_jobs=auto_run_jobs)


def default_sync_worker(store: HostedStore, org_id: str, project_id: str, job_id: str) -> dict[str, Any]:
    project = store.projects[project_id]
    source_records = [item for item in store.sources.values() if item["project_id"] == project_id]
    with tempfile.TemporaryDirectory(prefix="docpull-hosted-sync-") as tmp:
        workspace = Path(tmp)
        _hydrate_project_workspace(workspace, project, source_records)
        before = _latest_hosted_run_id(store, project_id)
        sync_payload = sync_project(root=workspace)
        diff_payload: dict[str, Any] | None = None
        if before:
            try:
                diff_payload = diff_project(
                    from_run_id=before,
                    to_run_id=str(sync_payload["run_id"]),
                    semantic="off",
                    root=workspace,
                )
            except ProjectError:
                diff_payload = None
        review_payload = review_project_run(run_id=str(sync_payload["run_id"]), root=workspace)
        run_artifact = workspace / ".docpull" / "runs" / str(sync_payload["run_id"])
        stored_artifact = store.artifact_root / project_id / "runs" / str(sync_payload["run_id"])
        stored_artifact.parent.mkdir(parents=True, exist_ok=True)
        if stored_artifact.exists():
            shutil.rmtree(stored_artifact)
        shutil.copytree(run_artifact, stored_artifact)
    run_record = {
        "id": sync_payload["run_id"],
        "org_id": org_id,
        "project_id": project_id,
        "job_id": job_id,
        "artifact_dir": str(stored_artifact),
        "payload": sync_payload,
        "review": review_payload,
        "created_at": utc_now_iso(),
    }
    store.runs[str(sync_payload["run_id"])] = run_record
    project["latest_run_id"] = sync_payload["run_id"]
    if diff_payload:
        diff_id = f"{diff_payload['from_run_id']}..{diff_payload['to_run_id']}"
        store.diffs[diff_id] = {
            "id": diff_id,
            "project_id": project_id,
            "generated_at": utc_now_iso(),
            "payload": diff_payload,
        }
    return run_record


def sign_webhook_payload(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()
    return f"sha256={digest}"


def verify_webhook_signature(body: bytes, secret: str, signature: str) -> bool:
    return hmac.compare_digest(sign_webhook_payload(body, secret), signature)


def _hydrate_project_workspace(
    workspace: Path,
    project: dict[str, Any],
    source_records: list[dict[str, Any]],
) -> None:
    init_project(name=str(project["name"]), root=workspace)
    from .project import load_project_config

    config = load_project_config(workspace)
    sources = [ProjectSource.model_validate(record["source"]) for record in source_records]
    config = config.model_copy(update={"sources": sources})
    save_project_config(workspace, config)


def _latest_hosted_run_id(store: HostedStore, project_id: str) -> str | None:
    runs = [item for item in store.runs.values() if item["project_id"] == project_id]
    if not runs:
        return None
    return str(sorted(runs, key=lambda item: str(item["created_at"]))[-1]["id"])


def _hosted_source_public(source: ProjectSource) -> dict[str, Any]:
    payload = source.model_dump(mode="json", exclude={"auth"})
    payload["auth"] = {
        "type": source.auth.type if source.auth else "none",
        "policy": source.auth.policy if source.auth else "none",
        "credential": "[env]" if source.auth else None,
        "ready": bool(source.auth is None or source.auth.env),
    }
    return payload


def _webhook_public(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "project_id": record["project_id"],
        "url": record["url"],
        "events": record["events"],
        "created_at": record["created_at"],
        # Response placeholder, not a credential.
        "secret": "[redacted]",  # nosec B105
    }


def _project_update(data: dict[str, Any]) -> dict[str, Any]:
    update: dict[str, Any] = {"updated_at": utc_now_iso()}
    if "name" in data:
        name = str(data["name"]).strip()
        if not name:
            raise HostedAPIError(400, "Project name must not be empty")
        update["name"] = name
    return update


def _path_parts(scope: ASGIScope) -> list[str]:
    return [unquote(part) for part in str(scope.get("path") or "/").strip("/").split("/") if part]


def _headers(scope: ASGIScope) -> dict[str, str]:
    return {key.decode("latin1").lower(): value.decode("latin1") for key, value in scope.get("headers", [])}


def _ensure_body_size_header(scope: ASGIScope, max_bytes: int) -> None:
    content_length = _headers(scope).get("content-length")
    if content_length is None:
        return
    try:
        size = int(content_length)
    except ValueError as err:
        raise HostedAPIError(400, "Invalid Content-Length header") from err
    if size > max_bytes:
        raise HostedAPIError(413, "Request body too large")


async def _handle_lifespan(receive: ASGIReceive, send: ASGISend) -> None:
    while True:
        message = await receive()
        message_type = message.get("type")
        if message_type == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message_type == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


async def _read_body(receive: ASGIReceive, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        chunk = bytes(message.get("body") or b"")
        total += len(chunk)
        if total > max_bytes:
            raise HostedAPIError(413, "Request body too large")
        chunks.append(chunk)
        if not message.get("more_body"):
            break
    return b"".join(chunks)


async def _json_response(send: ASGISend, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _json_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        value = json.loads(body)
    except json.JSONDecodeError as err:
        raise HostedAPIError(400, f"Invalid JSON body: {err}") from err
    if not isinstance(value, dict):
        raise HostedAPIError(400, "JSON body must be an object")
    return value


__all__ = [
    "HostedASGIApp",
    "HostedAPIError",
    "HostedStore",
    "POSTGRES_SCHEMA_SQL",
    "create_hosted_app",
    "default_sync_worker",
    "sign_webhook_payload",
    "verify_webhook_signature",
]
