# Deploy Shared Living

Use the [root README](README.md) for the Windows development launcher, including LocalStack. Use a fresh database and private object bucket for a new deployment. The earlier single-room deployment can remain available independently.

## Backend

Copy `backend/.env.example` to `backend/.env` on the deployment host. Set `ENVIRONMENT=production`, `LEGACY_ROOM=false`, a unique random JWT secret, and dedicated database credentials. Set `MYSQL_URL` and `MONGO_URL` to the addresses reachable from the API container. Allow only the deployed browser or Tauri origins in `CORS_ORIGINS`.

Choose a private S3-compatible bucket:

- For **Cloudflare R2**, use the account’s S3 endpoint, set `S3_REGION=auto`, and supply bucket-scoped credentials.
- For **Supabase S3**, enable S3 access, use the project’s HTTPS S3 endpoint including `/storage/v1/s3`, and set the project’s region and S3 credentials.
- For **MinIO**, use a private bucket behind HTTPS. If the API and desktop use different origins, set `S3_PUBLIC_ENDPOINT` to the desktop-accessible HTTPS origin and configure the proxy to preserve the signed host, path, and query.

Set `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, and `S3_SECRET_KEY`. Remove the development `S3_PUBLIC_ENDPOINT` when the provider uses the same endpoint for both clients. Configure bucket CORS for browser clients and allow `PUT`, `GET`, and the signed request headers. [Supabase S3 authentication](https://supabase.com/docs/guides/storage/s3/authentication)

With database services already running, build and start the API from `backend`. Put the container on the database network when running the databases with Compose. Set the two database URLs accordingly.

```powershell
docker compose up -d --wait mysql mongo
docker build -t shared-living-api .
docker run -d --name shared-living-api --network backend_default --restart unless-stopped --env-file .env -p 127.0.0.1:8000:8000 shared-living-api
```

Terminate TLS at a reverse proxy and enable WebSocket upgrades at `/ws`. Keep the API on one worker so connected users share the same notification hub. Exclude WebSocket query strings and signed storage URLs from logs. Check `/health/ready`, back up both databases and the bucket, and expire abandoned `staging/` uploads. Use versioned SQL migrations for later schema changes.

## Windows packaging

From `frontend`, set `VITE_API_URL` to the deployed HTTPS API URL ending in `/api/v1` and `VITE_WS_URL` to the server’s WSS URL. Set `VITE_LEGACY_ROOM=false`. In `src-tauri/capabilities/default.json`, add the exact API and storage URL patterns to the HTTP permission. Keep only the deployed origins in the release capability. Update the opener scope if the build opens signed download links externally.

```powershell
npm.cmd ci
npm.cmd run tauri build
```

The output under `frontend/src-tauri/target/release/bundle` includes a WiX `.msi` and an NSIS `.exe`. Run `start.ps1 -BuildExe` from the root for NSIS only. Sign the installer when a Windows code-signing certificate is available, test it on a clean Windows computer, and distribute it through a trusted private channel. Users register in the app and join with their household’s invite code.
