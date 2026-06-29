"""Hosted DocPull control-plane API tests."""

from __future__ import annotations

import json
from typing import Any

import pytest

from docpull.hosted import (
    HostedStore,
    create_hosted_app,
    sign_webhook_payload,
    verify_webhook_signature,
)


async def _asgi_request(
    app: Any,
    method: str,
    path: str,
    *,
    token: str = "test-token",
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    body_bytes = b"" if body is None else json.dumps(body).encode("utf-8")

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "query_string": b"",
        },
        receive,
        send,
    )
    status = next(item["status"] for item in messages if item["type"] == "http.response.start")
    response_body = b"".join(
        item.get("body", b"") for item in messages if item["type"] == "http.response.body"
    )
    return int(status), json.loads(response_body or b"{}")


async def _asgi_request_with_receive(
    app: Any,
    scope: dict[str, Any],
    receive: Any,
) -> tuple[int, dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    status = next(item["status"] for item in messages if item["type"] == "http.response.start")
    response_body = b"".join(
        item.get("body", b"") for item in messages if item["type"] == "http.response.body"
    )
    return int(status), json.loads(response_body or b"{}")


@pytest.mark.asyncio
async def test_hosted_project_source_crud_and_org_isolation() -> None:
    store = HostedStore(api_keys={"test-token": "org_a", "other-token": "org_b"})
    app = create_hosted_app(store=store, auto_run_jobs=False)

    status, project = await _asgi_request(app, "POST", "/v1/projects", body={"name": "docs"})
    assert status == 201

    status, source = await _asgi_request(
        app,
        "POST",
        f"/v1/projects/{project['id']}/sources",
        body={
            "name": "stripe",
            "url": "https://docs.stripe.com",
            "type": "html",
            "auth": {
                "type": "bearer_env",
                "env": "STRIPE_DOCS_TOKEN",
                "policy": "explicit-private",
            },
        },
    )
    assert status == 201
    assert source["auth"]["credential"] == "[env]"
    assert source["auth"]["type"] == "bearer_env"
    assert "STRIPE_DOCS_TOKEN" not in json.dumps(source)

    status, _payload = await _asgi_request(app, "GET", f"/v1/projects/{project['id']}", token="other-token")
    assert status == 404

    status, deleted = await _asgi_request(
        app,
        "DELETE",
        f"/v1/projects/{project['id']}/sources/{source['id']}",
    )
    assert status == 200
    assert deleted["deleted"] is True


@pytest.mark.asyncio
async def test_hosted_sync_job_webhook_export_and_release() -> None:
    store = HostedStore(api_keys={"test-token": "org_a"})

    def fake_worker(worker_store: HostedStore, org_id: str, project_id: str, job_id: str) -> dict[str, Any]:
        run_id = "run_fake"
        payload = {
            "id": run_id,
            "org_id": org_id,
            "project_id": project_id,
            "job_id": job_id,
            "payload": {"run_id": run_id, "summary": {"document_count": 1}},
            "created_at": "2026-06-24T00:00:00+00:00",
        }
        worker_store.runs[run_id] = payload
        worker_store.projects[project_id]["latest_run_id"] = run_id
        worker_store.diffs["diff_fake"] = {
            "id": "diff_fake",
            "project_id": project_id,
            "generated_at": "2026-06-24T00:00:00+00:00",
            "payload": {"summary": {"changed_count": 0}},
        }
        return payload

    app = create_hosted_app(store=store, worker=fake_worker)
    status, project = await _asgi_request(app, "POST", "/v1/projects", body={"name": "docs"})
    assert status == 201

    status, webhook = await _asgi_request(
        app,
        "POST",
        f"/v1/projects/{project['id']}/webhooks",
        body={"url": "https://example.com/hook", "events": ["run.completed"]},
    )
    assert status == 201
    assert webhook["secret"]

    status, job = await _asgi_request(app, "POST", f"/v1/projects/{project['id']}/syncs", body={})
    assert status == 202
    assert job["status"] == "succeeded"
    assert store.webhook_deliveries
    delivery = store.webhook_deliveries[0]
    body = json.dumps(delivery["payload"], sort_keys=True, ensure_ascii=False).encode("utf-8")
    assert verify_webhook_signature(body, webhook["secret"], delivery["signature"])

    status, runs = await _asgi_request(app, "GET", f"/v1/projects/{project['id']}/runs")
    assert status == 200
    assert runs["runs"][0]["id"] == "run_fake"

    status, diff = await _asgi_request(app, "GET", f"/v1/projects/{project['id']}/diffs/latest")
    assert status == 200
    assert diff["payload"]["summary"]["changed_count"] == 0


@pytest.mark.asyncio
async def test_hosted_rejects_invalid_auth_before_reading_body() -> None:
    app = create_hosted_app(store=HostedStore(api_keys={"test-token": "org_a"}), auto_run_jobs=False)

    async def receive() -> dict[str, Any]:
        raise AssertionError("body should not be read before authentication")

    status, payload = await _asgi_request_with_receive(
        app,
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/projects",
            "headers": [(b"authorization", b"Bearer wrong-token")],
            "query_string": b"",
        },
        receive,
    )

    assert status == 401
    assert payload["error"] == "Invalid API key"


@pytest.mark.asyncio
async def test_hosted_rejects_oversized_body_before_reading_body() -> None:
    app = create_hosted_app(store=HostedStore(api_keys={"test-token": "org_a"}), auto_run_jobs=False)

    async def receive() -> dict[str, Any]:
        raise AssertionError("oversized body should be rejected from Content-Length")

    status, payload = await _asgi_request_with_receive(
        app,
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/projects",
            "headers": [
                (b"authorization", b"Bearer test-token"),
                (b"content-length", b"1048577"),
            ],
            "query_string": b"",
        },
        receive,
    )

    assert status == 413
    assert payload["error"] == "Request body too large"


@pytest.mark.asyncio
async def test_hosted_export_rejects_traversal_run_id() -> None:
    store = HostedStore(api_keys={"test-token": "org_a"})
    app = create_hosted_app(store=store, auto_run_jobs=False)
    status, project = await _asgi_request(app, "POST", "/v1/projects", body={"name": "docs"})
    assert status == 201

    status, payload = await _asgi_request(
        app,
        "POST",
        f"/v1/projects/{project['id']}/exports/context-pack",
        body={"target": "cursor", "run_id": "../other-project/run"},
    )

    assert status == 400
    assert "run ID" in payload["error"]


@pytest.mark.asyncio
async def test_hosted_export_requires_run_ownership() -> None:
    store = HostedStore(api_keys={"test-token": "org_a"})
    app = create_hosted_app(store=store, auto_run_jobs=False)
    status, project = await _asgi_request(app, "POST", "/v1/projects", body={"name": "docs"})
    assert status == 201
    other_project = {
        "id": "proj_other",
        "org_id": "org_a",
        "name": "other",
        "created_at": "2026-06-24T00:00:00+00:00",
        "updated_at": "2026-06-24T00:00:00+00:00",
    }
    store.projects[other_project["id"]] = other_project
    store.runs["run_other"] = {
        "id": "run_other",
        "project_id": other_project["id"],
        "artifact_dir": str(store.artifact_root / other_project["id"] / "runs" / "run_other"),
    }

    status, payload = await _asgi_request(
        app,
        "POST",
        f"/v1/projects/{project['id']}/exports/context-pack",
        body={"target": "cursor", "run_id": "run_other"},
    )

    assert status == 404
    assert payload["error"] == "Run not found"


def test_webhook_signature_rejects_tampering() -> None:
    body = b'{"event":"run.completed"}'
    signature = sign_webhook_payload(body, "secret")

    assert verify_webhook_signature(body, "secret", signature)
    assert not verify_webhook_signature(b'{"event":"diff.detected"}', "secret", signature)
