# musicorg TypeScript client

When the `musicorgd` daemon ships (see [_organizer/LIBRARY_PLAN.md §7 Step 4](../../_organizer/LIBRARY_PLAN.md)), this directory will contain an auto-generated TypeScript client for embedding musicorg in:

- **Electron / Tauri desktop apps** — local-only daemon over `http://127.0.0.1:<port>`.
- **Browser web apps** — same shape, with `fetch()` and `WebSocket` for `/events`.
- **React Native / mobile apps** — REST + WebSocket over the network.

## Generating the client (when daemon is available)

```bash
# Start the daemon (single-tenant, localhost-only — no auth needed for local use).
musicorgd --library /path/to/library --port 30021 &

# Generate types + client from the live OpenAPI schema.
npx openapi-typescript http://127.0.0.1:30021/openapi.json -o client.ts
```

The output `client.ts` gives you fully-typed `fetch` wrappers for every endpoint, plus the Pydantic request/response schemas as TypeScript interfaces. Drop it straight into your Electron or web project — no further setup.

## REST route map (from PUBLIC_API.md)

The daemon maps the core library functions to REST routes as follows:

| Route | Core function | Notes |
|-------|--------------|-------|
| `POST /v1/scan` | `scan()` | Returns job ID; use `/v1/jobs/{id}` or WS to follow progress |
| `POST /v1/dedupe` | `group_duplicates()` + `write_dedupe_outputs()` | |
| `POST /v1/resolve` | `resolve_winners()` | |
| `POST /v1/plan` | `plan()` | |
| `POST /v1/canonicalize/diff` | `build_diff()` | |
| `POST /v1/canonicalize/apply` | `apply_canonical()` | |
| `POST /v1/canonicalize/approvals` | `apply_approvals()` | |
| `POST /v1/execute` | `execute_plan()` | |
| `POST /v1/upgrade` | `upgrade_batch()` | |
| `GET  /v1/jobs/{id}` | Progress via `ProgressEvent` stream | Poll or use WS |
| `WS   /v1/events` | `ProgressEvent` WebSocket bridge | Real-time push |

## Why no published npm package?

We ship the generation recipe rather than a `@musicorg/client` package on npm. Tradeoffs:

- **Zero maintenance burden** for the musicorg team — the daemon's OpenAPI schema is the source of truth, and the TypeScript client is mechanically derived.
- **You always get types matching your daemon version** — generate against your running daemon, not against whatever was last published.
- **One extra command** at your project setup — `npx openapi-typescript`. We consider that a fair trade.

If your team prefers a published package, wrap the generator output and publish it in your own private registry.

## See also

- [_organizer/LIBRARY_PLAN.md](../../_organizer/LIBRARY_PLAN.md) §5 Refactor 5 — OpenAPI schema requirements.
- [_organizer/WEB_APP_PLAN.md](../../_organizer/WEB_APP_PLAN.md) — the full web-app design that is the first non-CLI consumer.
- [../05_embed_in_fastapi.py](../05_embed_in_fastapi.py) — Python-side FastAPI skeleton showing the same WebSocket + job pattern.
