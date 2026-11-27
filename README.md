# Shared Living v2.0

Shared Living tracks personal income and spending, splits group expenses, and lets each creditor review payment receipts. The Windows desktop app uses the same API as the browser development build.

## Start on Windows

Install Docker Desktop with Docker Compose, Python 3.11+, and Node.js. Tauri also needs Rust, the Visual Studio C++ build tools, and WebView2. Run from the repository root in PowerShell.

```powershell
Set-Location D:\qlct
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

The launcher creates missing environment files, starts MySQL, MongoDB and LocalStack, waits for database and bucket health, opens Uvicorn in a separate window, and starts the desktop client. Without Cargo it serves the browser build at `http://127.0.0.1:1420`. The API listens at `http://127.0.0.1:8000`. Closing a client leaves the backend window and Docker services available.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -WebOnly
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -BuildExe
```

`-BuildExe` packages an NSIS installer without starting services. It uses the URLs already in `frontend/.env`. For a remote deployment, configure the actual HTTPS API and storage origins before building. See [DEPLOY.md](DEPLOY.md).

### Start each service manually

From the root, run the backend commands in one terminal. Run the final Uvicorn command after Docker reports the database and storage services healthy.

```powershell
Set-Location D:\qlct\backend
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d --wait
if (!(Test-Path ..\.venv\Scripts\python.exe)) { py -3 -m venv ..\.venv }
..\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers --timeout-keep-alive 5 --limit-concurrency 200 --limit-max-requests 5000 --backlog 512 --no-access-log
```

Open a second terminal for the frontend.

```powershell
Set-Location D:\qlct\frontend
if (!(Test-Path .env)) { Copy-Item .env.example .env }
npm.cmd ci
npm.cmd run tauri dev
```

Use `npm.cmd run dev` in place of the final command for the browser build.

## Verify a running stack

```powershell
Set-Location D:\qlct\backend
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m scripts.smoke_living
Set-Location D:\qlct\frontend
npm.cmd test
npm.cmd run build
```

The live smoke check registers two generated accounts, approves a group join, checks WebSocket delivery, creates a shared debt, verifies the VietQR amount, uploads and downloads a tiny JPEG through LocalStack, settles the debt, and checks analytics and the local advisor. Use it on a development database because it leaves synthetic records. For sample data instead, run `..\.venv\Scripts\python.exe -m scripts.seed --demo` from `backend` and save the printed passwords for group `GRP-DEMO01`. The demo and smoke QR details are synthetic.

To check the screens in two browser profiles, register two accounts. Create a group in one profile and request to join in the other. Approve the request, save a group VietQR for each member, and log a shared expense. Open **Debts & VietQR** as the debtor and submit a test receipt. Review it from the creditor's **Debts & VietQR** page or its group chat card. Add a personal transaction and a budget, then check **Overview** and **Advisor**. The advisor uses local calculations by default.

To enable the optional provider, put `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`) in `backend/.env`, or set `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` for an OpenAI-compatible provider. Restart the backend after changing settings. The Advisor sends the prompt and spending totals only when the user selects the sharing checkbox. Provider failures fall back to local analysis. [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
