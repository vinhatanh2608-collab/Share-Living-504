# Deploy Shared Living

Use the [root README](README.md) for the Windows development launcher, including LocalStack. Use a fresh database and private object bucket for a new deployment. The earlier single-room deployment can remain available independently.

## Backend

Copy `backend/.env.example` to `backend/.env` on the deployment host. Set `ENVIRONMENT=production`, `FORCE_HTTPS=true`, `LEGACY_ROOM=false`, a unique random JWT secret, and dedicated database credentials. Set `MYSQL_URL` and `MONGO_URL` to the addresses reachable from the API container. Set `CORS_ORIGINS` to your HTTPS frontend and packaged desktop origins, for example `["https://app.your-domain","https://tauri.localhost","tauri://localhost"]`. Remove all HTTP development origins.

Choose a private S3-compatible bucket:

- For **Cloudflare R2**, use the account’s S3 endpoint, set `S3_REGION=auto`, and supply bucket-scoped credentials.
- For **Supabase S3**, enable S3 access, use the project’s HTTPS S3 endpoint including `/storage/v1/s3`, and set the project’s region and S3 credentials.
- For **MinIO**, use a private bucket behind HTTPS. If the API and desktop use different origins, set `S3_PUBLIC_ENDPOINT` to the desktop-accessible HTTPS origin and configure the proxy to preserve the signed host, path, and query.

Set `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, and `S3_SECRET_KEY`. Remove the development `S3_PUBLIC_ENDPOINT` when the provider uses the same endpoint for both clients. Configure bucket CORS for browser clients and allow `PUT`, `GET`, and the signed request headers. [Supabase S3 authentication](https://supabase.com/docs/guides/storage/s3/authentication)

With database services already running, build and start the API from `backend`. Put the container on the database network when running the databases with Compose. Set the two database URLs accordingly.

```powershell
docker compose up -d --wait mysql mongo
docker build -t shared-living-api .
$proxyPeer = docker network inspect backend_default --format '{{(index .IPAM.Config 0).Gateway}}'
docker run -d --name shared-living-api --network backend_default --restart unless-stopped --env-file .env -e ("TRUSTED_PROXY_IPS=" + (ConvertTo-Json -InputObject @($proxyPeer, "127.0.0.1") -Compress)) -p 127.0.0.1:8000:8000 shared-living-api
```

Restrict port 8000 to the proxy host. For the example Docker bridge, Uvicorn trusts the bridge gateway and loopback. If Docker or Caddy uses a different network arrangement, set `TRUSTED_PROXY_IPS` to the exact peer addresses seen by Uvicorn. Avoid wildcard proxy trust. Run one API worker so the WebSocket hub and in-memory limits are shared.

## Domain and TLS

1. Create a DNS record for the API hostname pointing to the server. Allow inbound TCP 80 and 443 to Caddy. Keep the API, MySQL, and MongoDB ports private.
2. Build Caddy with the rate-limit module using `xcaddy`, or use a Caddy binary built with that module. Run these commands from the repository root. Enter the DNS hostname without a scheme or path.

   ```powershell
   xcaddy build --with github.com/mholt/caddy-ratelimit
   $env:API_DOMAIN = Read-Host "API DNS hostname"
   .\caddy.exe validate --config .\deploy\Caddyfile --adapter caddyfile
   .\caddy.exe run --config .\deploy\Caddyfile --adapter caddyfile
   ```

3. Check `curl.exe -I "http://$env:API_DOMAIN/health/ready"` for the HTTPS redirect and `curl.exe -i "https://$env:API_DOMAIN/health/ready"` for HTTP 200 and the security headers. Connect the client using `https://` for REST and `wss://` for WebSockets. Caddy handles the `/ws` upgrade through the same reverse proxy.

The proxy caps inbound bodies at 6 MB and applies a broad per-IP limit. FastAPI also applies its configurable global and route-specific limits. Receipt bytes go directly to the private object store using a short-lived PUT signed for their exact size and type. The API refuses oversized objects before downloading them and verifies bounded image bytes before writing a permanent attachment. Keep a staging-expiration policy and use separate development and production buckets. For a storage origin you operate, serve it over TLS and add `X-Content-Type-Options: nosniff` at its proxy.

Keep proxy and Uvicorn access logging disabled or redact WebSocket query strings and object signatures. Back up both databases and the private bucket, and use versioned SQL migrations for later schema changes. [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https) · [Caddy rate-limit module](https://github.com/mholt/caddy-ratelimit)

## Windows packaging

In `frontend/.env.production`, set `VITE_API_URL` to the deployed HTTPS API URL ending in `/api/v1`, `VITE_WS_URL` to the WSS URL ending in `/ws`, and `VITE_STORAGE_URL` to the HTTPS origin or Supabase S3 endpoint used in signed object URLs. Set `VITE_LEGACY_ROOM=false`. The build uses these URLs to configure both the Tauri CSP and the native API and storage permissions. The launcher preserves the release environment file.

```powershell
npm.cmd ci
npm.cmd run release:check
npm.cmd run tauri build
```

The output under `frontend/src-tauri/target/release/bundle` includes a WiX `.msi` and an NSIS `.exe`. Run `start.ps1 -BuildExe` from the root for NSIS only. Sign the installer when a Windows code-signing certificate is available, test it on a clean Windows computer, and distribute it through a trusted private channel. Users register in the app and join with their household’s invite code.

## Flood protection

Run Uvicorn with `--no-proxy-headers --timeout-keep-alive 5 --limit-concurrency 200 --limit-max-requests 5000 --backlog 512`. Configure the process supervisor to restart after the request ceiling. The application validates the original socket peer before accepting forwarding headers. Caddy overwrites X-Forwarded-For and removes client-supplied CF-Connecting-IP on direct ingress.

For Cloudflare, enable proxied DNS and Full (strict) TLS. Apply the Free Managed Ruleset. Use the available rate-limiting rule for repeated POST requests to the login and registration paths, grouped by client IP. Choose thresholds and mitigation durations available in your dashboard. Restrict origin ingress to Cloudflare's published IP networks when using its proxy. Configure Caddy trusted_proxies with those networks and forward its validated client_ip in place of remote_host for both the rate-limit key and X-Forwarded-For. Otherwise Cloudflare POP addresses share a budget.

During a volumetric attack, enable Cloudflare Under Attack mode on browser-facing paths. Interactive challenges can disrupt desktop API clients. Keep the origin firewall restricted to the edge and use edge rate limits for API traffic. Each Uvicorn worker has its own admission budget and database pools.
