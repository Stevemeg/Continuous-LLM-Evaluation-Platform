# Dependencies and why each one is here

`REQ-N-MAINT-5` requires every dependency to carry a justification. A lockfile
records what is installed; it does not record why, and "why" is the thing that
decays. Phase 3 deliberately created no manifest; Phase 5 creates the first one.

Nothing below was added for convenience. Anything not named in canonical §15 or
required by an accepted ADR is not present.

## Runtime

| Dependency | Why it is here | Rejected alternative, and why |
|---|---|---|
| `fastapi` | HTTP surface for the OpenAPI 3.1 contract, which is authoritative. Named in canonical §15. | A hand-rolled ASGI router. Rejected: the contract has 13 operations and RFC 9457 problem responses; re-implementing content negotiation and validation is more code to get wrong, not less. |
| `pydantic` | Request and response models generated from, and checked against, the contract. Named in canonical §15. | `dataclasses` with hand-written validation. Rejected: the contract specifies patterns, enums and nullability that would become hand-maintained duplicates — exactly the drift Phase 3 removed. |
| `uvicorn` | ASGI server to run the application. | None considered; a server is required and this is FastAPI's documented default. |
| `psycopg` | PostgreSQL driver. [ADR-012](adr/ADR-012-primary-datastore.md) selected PostgreSQL under four conditions, one of which is a runtime role that cannot bypass row-level security; the driver must be able to set a per-transaction tenant context. | An ORM. Rejected: the schema is the authority and carries the invariants as constraints. An ORM would introduce a second model of the same tables, and its migration autogeneration would invite implementation to redefine the schema — the thing schema governance forbids. |
| `arq` | Task queue for durable execution. [ADR-001](adr/ADR-001-durable-execution.md) selected a task queue with explicit checkpointing over a durable workflow engine, on measured evidence. | A durable workflow engine. Rejected in ADR-001 on operational footprint, and because run state in its own datastore would sit outside the row-level-security boundary ADR-012 established. |
| `redis` | Broker for `arq`; also the cache/ephemeral coordination store named in canonical §15. | None; `arq` requires it and §15 names it. |

## Build

| Dependency | Why it is here | Rejected alternative, and why |
|---|---|---|
| `setuptools` | Build backend, so the package installs into an environment rather than being imported from a path. Nothing in the running system imports it. | Poetry or Hatch. Rejected: both would be a second project-configuration format alongside `pyproject.toml`, for a package with one source directory and no build steps. |

## Development only

| Dependency | Why it is here |
|---|---|
| `pytest`, `pytest-asyncio` | The test suite. Canonical §20. |
| `pytest-cov` | `REQ-N-MAINT-4` requires a coverage gate, which requires a coverage measurement. |
| `httpx` | Drives the API in tests without binding a socket, so contract tests need no running server. |

## Deliberately absent

| Not used | Why not |
|---|---|
| A provider-aggregation library | [ADR-003](adr/ADR-003-provider-abstraction.md) rejected it on measured evidence: it reports identical structured signals for an outage and a malformed response, and it wrote the API key to stdout under debug logging. |
| A migration framework | The schema in [`data/schema/`](data/schema/) is the authority. The runner in `src/clep/db/migrations.py` applies those files directly and refuses to run if an already-applied file has changed. A framework's autogeneration would let implementation redefine the schema. |
| An HTTP client library in the provider adapter | The adapter uses the standard library, so the egress path has no third-party code between the domain and the provider. `REQ-N-SEC-5` is easier to guarantee when fewer components can log. |

## The rule this table exists to enforce

A dependency is a decision. Adding one to make something work today, without a
line here, is how a system acquires components nobody can later justify or
remove.
