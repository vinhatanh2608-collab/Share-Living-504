# Dormitory Desk

Tauri v2 desktop client for the eight-roommate FastAPI workspace. This package ships both `frontend/` and the backward-compatible `backend/` update. Existing accounts and data can be kept; no SQL schema migration is needed for these changes.

## Run locally

1. Start the included backend using [its setup guide](../backend/README.md). Use the updated backend with `/api/v1`, custom split validation, and batch-proof endpoints.
2. If you already have a backend `.env`, add the desktop and Vite origins from the new backend `.env.example` to `CORS_ORIGINS`, then restart FastAPI. Keep development origins out of your production deployment.
3. Install Node.js LTS, stable Rust, and the [Tauri platform prerequisites](https://v2.tauri.app/start/prerequisites/). Run:

```sh
cd frontend
cp .env.example .env
npm ci
npm run tauri -- dev
```

On PowerShell use `Copy-Item .env.example .env`. For browser-only development use `npm run dev`; signed storage endpoints need browser CORS configured for GET/PUT and Content-Type. The desktop client uses the native HTTP plugin for API and storage requests, so object storage does not need browser CORS for desktop use.

## Release

- Set `VITE_API_URL=https://your-api.example/api/v1` and `VITE_WS_URL=wss://your-api.example/ws` before compiling. Add the exact API and storage origins to the Tauri HTTP permissions and the storage origin to the opener permissions in `src-tauri/capabilities/default.json`. Scope production URLs to your API and your R2 account; remove local scopes and tighten CSP `connect-src` in `src-tauri/tauri.conf.json`.
- Launch Windows and Linux builds **on their target platforms**. Run `npm run tauri -- build` and find bundles in `src-tauri/target/release/bundle/`. Sign Windows installers through your release process. Use production TLS/WSS; do not log WebSocket token query strings or signed storage URLs. See the backend deployment checklist as well.
- Enable desktop alerts in the workspace. Alerts require OS permission and a running app; they are **not background push** when the app is closed. JWT stays only in process memory and is cleared on sign-out or expiry.
- Before release, test the signed installer on each target OS with a real backend: sign-in, realtime reconnect, native notification delivery, signed uploads/downloads, VietQR, and owner-only proof review. Confirm the expected Tauri WebView origin if WebSocket authentication fails.

## Finance behavior

- Equal and custom splits are validated on the server. The payer may be included in the split, but does not owe themselves.
- Select up to 25 open debts owed to **one creditor**. Each debt can show its original VietQR; one JPEG receipt attaches to the complete batch. The app compresses that image to below 300 KB before upload. Submission is all-or-nothing for the debt/receipt records and remains pending until review.
- **Only the configured owner** can approve or reject. A QR code, receipt upload, or chat event is not confirmation of a bank transfer. Rejected debts reopen; approved debts are settled.

## Checks

```sh
npm run build
npm test
```

The included backend tests use SQLite for API transactions and replace only the remote copy in batch tests. Validate locking, outbox delivery, and object storage against live services before deploying.
The [root README](../README.md) covers Shared Living v2 startup on port 1420. The original room dashboard described below requires `VITE_LEGACY_ROOM=true` and a backend started with `LEGACY_ROOM=true`.

