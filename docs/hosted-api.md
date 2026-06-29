# Hosted API Contract

DocPull is hybrid: the OSS CLI remains the local evidence engine, and the
hosted API is the managed control plane for projects, sync jobs, diffs,
context-pack exports, releases, and webhooks.

The hosted MVP is implemented as a dependency-free Python ASGI app in
`docpull.hosted`. It is separate from `docpull serve`, which stays a read-only
localhost pack API.

## Authentication

Hosted API requests use org-scoped Bearer API keys:

```text
Authorization: Bearer <api-key>
```

Every project-scoped endpoint enforces org ownership. Source credentials are
accepted only as environment-variable references in v1; raw source secrets are
never accepted in project payloads.

Remote CLI clients store org-scoped Bearer tokens only for HTTPS API URLs by
default. The `--allow-insecure-local-http` login flag is reserved for
localhost/loopback development endpoints and is rechecked before every remote
request, so old cleartext configs do not keep sending tokens.

## Endpoints

```text
POST   /v1/projects
GET    /v1/projects
GET    /v1/projects/{project_id}
PATCH  /v1/projects/{project_id}
DELETE /v1/projects/{project_id}

POST   /v1/projects/{project_id}/sources
GET    /v1/projects/{project_id}/sources/{source_id}
PATCH  /v1/projects/{project_id}/sources/{source_id}
DELETE /v1/projects/{project_id}/sources/{source_id}

POST   /v1/projects/{project_id}/syncs
GET    /v1/jobs/{job_id}
GET    /v1/projects/{project_id}/runs
GET    /v1/projects/{project_id}/runs/{run_id}
GET    /v1/projects/{project_id}/diffs/latest

POST   /v1/projects/{project_id}/exports/context-pack
POST   /v1/projects/{project_id}/releases

POST   /v1/projects/{project_id}/webhooks
GET    /v1/projects/{project_id}/webhooks
DELETE /v1/projects/{project_id}/webhooks/{webhook_id}
```

## Storage Model

`docpull.hosted.POSTGRES_SCHEMA_SQL` defines the intended managed metadata
tables: `orgs`, `api_keys`, `projects`, `sources`, `jobs`, `runs`, `diffs`,
`exports`, `releases`, `webhooks`, and `audit_events`.

The checked-in MVP uses an in-process `HostedStore` and filesystem artifact
root for local tests. Production should swap that store for Postgres metadata,
object storage for `.docpull/runs/<run_id>/...` artifacts, and queued workers
for sync execution.

Export and release routes resolve run artifacts through recorded run ownership
and require the artifact path to remain under the current project's runs root.
Request bodies are authenticated before buffering and capped to avoid
unauthenticated memory growth.

## Webhooks

Webhook deliveries are signed:

```text
X-DocPull-Signature: sha256=<hmac>
```

Payloads omit document content by default and include event metadata plus run,
diff, or release summaries.
