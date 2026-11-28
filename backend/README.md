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

## Database operations

Install backend dependencies in the repository virtual environment first. Run these commands from the repository root unless stated otherwise.

### Normal startup

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\start.ps1 -WebOnly
```

To start the data services alone, run `docker compose up -d --wait` inside `backend`. The default profile starts MySQL, MongoDB, Redis and LocalStack. Redis is memory bounded and bound to loopback. Set `REDIS_URL=` to disable it. On a Redis error the API reads from MySQL and uses local request counters. WebSocket state and login cooldowns remain local to a process, so continue to use one API worker.

Use `DATABASE_URL` or the existing `MYSQL_URL` for the primary. Keep `DATABASE_READ_URL` unset in normal mode. MySQL queries and MongoDB operations have configurable time limits, and the request deadline is separate. Cache entries expire after a short TTL. Successful relevant writes rotate a shared cache generation.

### Optional MySQL replica

Run from `backend`, with the API stopped for the initial seed so schema changes cannot overlap the snapshot:

```powershell
docker compose --profile ha up -d --wait
..\.venv\Scripts\python.exe -m scripts.setup_mysql_replica
docker compose --profile ha exec mysql-replica mysql -uroot -p -e 'SHOW REPLICA STATUS\G'
```

The seed command requires an empty replica database and leaves the primary data in place. Check `Replica_IO_Running` and `Replica_SQL_Running` before enabling the reader. Set `DATABASE_READ_URL` in `.env` to the replica URL on port 3307 and restart the API. The replica account needs `REPLICATION CLIENT` for the health and lag check. Analytics and monthly report reads can use it. Identity, membership and writes use the primary. If the reader is down or more than five seconds behind, the API falls back to primary and retries the reader with backoff. Writes require a working primary.

### Cold archive

Run from `backend`:

```powershell
..\.venv\Scripts\python.exe -m app.scripts.archive_old_data --batch-size 100 --max-batches 10
```

This moves personal records and eligible fully settled group expenses older than 12 calendar months, in batches. Historical lists, reports and expense retry IDs continue to work. Group expenses linked to fund transactions or chat drafts stay in the active tables to preserve their references. Add `--retention-days 365` only if you want to permanently delete chat and AI history older than 365 days. By default, document history is retained.

### Backups and restore

```powershell
.\backend\scripts\backup_db.ps1
.\backend\scripts\backup_db.ps1 -RegisterTask
```

The Windows task runs at 02:00 local machine time under the current user and needs access to Docker. The API also checks daily from 02:00 Vietnam time while it is running. Both use the same lock and daily archive check. On Linux/macOS run `sh backend/scripts/backup_db.sh` or schedule that command with `--daily`. Archives are stored in `backend/backups/`. Retention defaults to 14 archives and 14 days, controlled by `BACKUP_KEEP_COUNT` and `BACKUP_RETENTION_DAYS`. The manifest records whether MongoDB was running and included. LocalStack objects are outside these database dumps.

To restore a trusted backup, stop the API, leave the destination Compose database services running, and explicitly confirm replacement. The restore affects the database tables and collections present in the dumps and is not atomic across the two databases. Verify backups against a disposable database before relying on them.

```powershell
.\backend\scripts\backup_db.ps1 -Restore .\backend\backups\shared_living_YYYYMMDD_HHMMSS.zip -ConfirmReplace
```

On Linux/macOS run `sh backend/scripts/backup_db.sh --restore PATH --confirm-replace`.
