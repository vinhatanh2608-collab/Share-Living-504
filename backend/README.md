# Dormitory Room API

Shared Living v2 starts in multi-group mode by default. See the [root README](../README.md) for the active startup and test commands. This page documents the legacy API, available only with `LEGACY_ROOM=true`.

Async FastAPI backend for one room with eight pre-provisioned accounts. No public registration.

## Local setup

Requires Python 3.11+ and Docker Compose v2. From the extracted project:

```sh
cd backend
cp .env.example .env
# Fill in service credentials and generate a signing key as described in .env.example.
docker compose up -d --wait
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
LEGACY_ROOM=true python -m scripts.seed --demo
LEGACY_ROOM=true uvicorn app.main:app --host 127.0.0.1 --workers 1 --ws-max-size 4096 --no-access-log
```

Seed prints newly generated demo passwords once. Demo bank details are synthetic; do not send money to them. Open [Swagger UI](http://127.0.0.1:8000/docs). Log in with JSON at `POST /auth/login`; use the returned bearer token. Set a real random JWT key and unique service credentials for any shared environment.

## Desktop integration

The updated backend also serves all REST endpoints under `/api/v1`, without removing the unprefixed routes. WebSocket clients still use `/ws`. Update `CORS_ORIGINS` in any existing `.env` using the new example before connecting a desktop development build.

| Operation | Endpoint / contract |
| --- | --- |
| Account and directory | `GET /auth/me`, `GET /auth/roommates`, `PUT /auth/me/vietqr` |
| Expense | `POST /finance/expenses`, caller is the payer; client generates a UUID `request_id` and reuses the exact body on retries |
| Debts | `GET /finance/debts` or `GET /finance/overdue`; advance `after` to the last returned ID |
| Payment | `GET /finance/debts/{id}/vietqr`; the URL goes to the external VietQR image service, not a bank verification API |
| Receipt | `POST /finance/debts/{id}/proof-upload`, PUT raw bytes using the signed headers, then `POST /finance/debts/{id}/proofs` with the returned `upload_id` |
| Batch receipt | `POST /finance/proof-batches/upload` with `debt_ids`, `content_type` and `size`; PUT raw bytes, then `POST /finance/proof-batches` with the same `debt_ids` and `upload_id`. Up to 25 open debts for one creditor; individual proof review remains unchanged. |
| Owner review | `GET /finance/proofs/pending`, `GET /finance/proofs/{id}`, `POST /finance/proofs/{id}/decision` with `{"approve": true}` or `false` |
| Reminders | `POST /finance/debts/{id}/reminder` by creditor or owner; online WebSocket notification, once per 24 hours per debt |
| Chat | `POST /chat/messages` with `{"text":"..."}`; `GET /chat/messages?before=...` with the returned cursor |
| Study | `POST /study/uploads`, PUT bytes, then `POST /study/materials`; list/search with `q`, fetch/download, update metadata, or delete |
| Shared notes | `GET /study/notes/{name}`, then PUT `text` and `expected_version`; a concurrent edit returns 409 |
| Realtime | `/ws` with an Authorization header (preferred) or `?token=...`; after reconnect or `resync`, re-fetch REST state |

Amounts are integer VND. Splits allocate remainders by ascending participant ID; include the payer in the participant list if the payer shares the cost. Timestamps in finance requests require a timezone. Payment cards are append-only events visible to the debtor, creditor, and owner; use `event_id` to de-duplicate and prefer the highest event ID per proof. Receipt downloads are restricted to the uploader and owner.

To use a custom expense split, also send `custom_shares` as a JSON object mapping participant IDs to positive integer VND amounts. The keys must match the selected participants exactly and the shares must sum to the total. Both new finance features reuse the existing SQL tables.

The approval transaction locks the debt and proof rows and commits the proof decision, debt state, and outbox record together. MongoDB projection is retryable and keyed by outbox ID. WebSockets are best-effort notifications, not the source of truth. Rejected proofs reopen the debt and retain the review record; a new upload can then be submitted.

## Deployment checklist

- Set `ENVIRONMENT=production`, HTTPS/WSS, unique credentials, restricted MongoDB/MySQL network access and an allowlisted `CORS_ORIGINS`. Keep `OWNER_USERNAME` stable. Provision with `python -m scripts.seed` and `OWNER_PASSWORD` supplied through a secret; securely distribute the generated roommate passwords. Do not use `--demo`.
- Use one API worker/replica for the in-process WebSocket hub. For horizontal scaling, add a shared event bus and keep the MySQL outbox. Do not log WebSocket query strings, signed download URLs, or login bodies.
- Use Cloudflare R2's private S3 API endpoint as `S3_ENDPOINT`, set `S3_REGION=auto`, create the bucket and issue bucket-scoped credentials. For MinIO behind a proxy, `S3_PUBLIC_ENDPOINT` can differ from the internal endpoint. Do not rewrite the signed URL host.
- Configure ingress login/upload rate limits, secrets rotation, backup/restore drills, and monitoring for API readiness, outbox lag and repeated delivery errors. The seed command bootstraps the initial SQL schema; use reviewed versioned migrations for subsequent deployments.
- Expire `staging/` objects with a bucket lifecycle rule. Material deletion removes its database record; use a grace-period reconciliation job for unreferenced finalized objects (including failed or concurrent submissions). Presigned downloads are bearer URLs valid for five minutes. File content is not malware-scanned; enable scanning before accepting untrusted files.

## Database indexes

Startup creates the finance and outbox indexes on new and existing MySQL tables under the schema lock. On large deployments, schedule the first startup with these indexes during a maintenance window because index creation can take time. MongoDB creates its chat and advisor indexes at startup. Review MySQL `EXPLAIN ANALYZE` and MongoDB `explain("executionStats")` against representative data when evaluating query performance.

## Tests

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

The suite checks VND splitting, approval authorization, idempotent settlement and outbox writes, OpenAPI, and WebSocket event audience. The API transaction tests use SQLite and do not validate MySQL row locking, MongoDB delivery, or live object-storage signing; run the full receipt flow against Compose and your R2 bucket before deployment.
