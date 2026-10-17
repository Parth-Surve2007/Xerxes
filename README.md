# Xerxes

Xerxes is a local-first computer agent for inspecting projects, proposing structured actions, executing a narrow allowlist of tools, verifying real results, and recording an append-only audit trail. Prompts, source files, credentials, and screen contents remain local by default. A LinuxONE service receives only structured action metadata and returns an advisory risk score; deterministic local policy remains authoritative.

## Working vertical slice

- React/Vite agent interface with task composer, proposed actions, execution results, workspace scope, runtime status, and security audit views.
- Flask agent API with task state, scoped workspace tree/search/read operations, allowlisted test/build execution, cancellation, and SQLite audit persistence.
- Local model adapter with explicit `ready`/`unavailable` states. No cloud fallback and no model is downloaded automatically. Without `XERXES_LOCAL_MODEL_URL`, Xerxes uses clearly labelled deterministic diagnostic planning.
- Policy enforcement rejects traversal, symlink-resolved paths outside the workspace, private key/credential paths, non-allowlisted commands, network-transfer tools, and unenabled destructive tools.
- LinuxONE-backed synthetic risk classifier using scikit-learn LogisticRegression and 32 deterministic synthetic action examples.

## Local run

```powershell
npm install
python -m pip install -r backend/requirements.txt
$env:XERXES_WORKSPACE=(Get-Location).Path
$env:XERXES_DATABASE="$((Get-Location).Path)\backend\xerxes.db"
python backend/app.py
```

In another terminal:

```powershell
npm run dev
```

The UI is `http://localhost:5173`. Copy `.env.example` to `.env` for configuration. A local model adapter can point at a local-only structured-output HTTP runtime with `XERXES_LOCAL_MODEL_URL`; the application reports unavailable when it is not configured.

## Windows desktop application

Electron starts the loopback Flask sidecar itself; opening a browser or starting `python backend/app.py` is not required:

```powershell
npm install
npm run desktop:dev
```

Create a distributable Windows build with `npm run desktop:dist`. The packaged app includes the agent backend source and requires a compatible local Python installation with the packages in `backend/requirements.txt`; it stores the SQLite database under the Windows application-data directory. The renderer has no Node integration, uses context isolation and sandboxing, and receives only a narrow API-base configuration through preload.

The desktop shell selects a free loopback port, starts and monitors the backend, and loads `dist/index.html` in packaged builds. `electron-builder` produces an NSIS installer and portable executable. No SSH key, `.env` file or secret is packaged.

Ollama integration uses `http://127.0.0.1:11434`. Set `XERXES_LOCAL_MODEL` to the installed model name; the default experimental target is `qwen2.5-coder:1.5b`. Xerxes reports `ready`, `missing`, or `unavailable` from the actual Ollama API and never downloads a model automatically.

## Native llama.cpp adapter

The C99 adapter is in `native/`. It calls the public llama.cpp API while the upstream engine remains C/C++. Build it against an upstream checkout with CMake and Ninja:

```powershell
cmake -S native -B native/build -G Ninja -DLLAMA_CPP_DIR=C:/path/to/llama.cpp
cmake --build native/build --config Release
native/build/xerxes-llm.exe MODEL.gguf "Return a JSON object with an actions array" 64
```

The verified local build used upstream llama.cpp `0.7.0-dev` with CPU-only MinGW. The core library and `xerxes-llm.exe` linked successfully. The upstream server target was not built because this MinGW SDK lacks `CreateFile2`; the adapter does not require that server target. Qwen's official model card identifies the Q4_K_M artifact as approximately 4.68 GB, Apache-2.0, and documents the official repository/download path. Weights are stored outside Git under `%LOCALAPPDATA%\Xerxes\models`.

## Tests

```powershell
npm run build
python -m pytest backend/tests risk_service/test_risk.py -q
$env:API_BASE_URL='http://127.0.0.1:5000/api/v1'; python backend/smoke_test.py
```

The tests cover real task execution, cross-platform allowlisted subprocesses, path traversal denial, adversarial instruction handling, audit records, risk schema validation, and synthetic classifier behavior.

## LinuxONE risk service

The risk service is deployed separately so the previous scheduler VM state remains intact:

- VM architecture: `s390x`; Python: `3.12.3`
- Remote directory: `/home/linux1/xerxes-risk-service`
- Loopback endpoint: `http://127.0.0.1:5001`
- systemd unit: `xerxes-risk.service`
- Model: `synthetic-risk-logreg-v1`, scikit-learn LogisticRegression with DictVectorizer
- Dataset: 32 deterministic synthetic examples; 26 high-risk and 6 low-risk labels
- Reproducible held-out evaluation: 24 training samples, 8 test samples, precision `1.0`, recall `1.0`, confusion matrix `[[1, 0], [0, 7]]`

The service uses Ubuntu’s native s390x NumPy/SciPy/scikit-learn packages and a system-site-packages venv, avoiding an x86-only wheel or an impractical source build. It exposes `GET /api/v1/health`, `GET /api/v1/model/info`, and `POST /api/v1/risk/score`. It is advisory and does not receive prompts, file contents, credentials, or source code.

For local integration, create the tunnel:

```powershell
ssh -N -T -i "$HOME\Downloads\Xerxes_key.pem" `
  -L 5001:127.0.0.1:5001 linux1@148.100.108.251
```

Then set `XERXES_RISK_URL=http://127.0.0.1:5001` before starting the local API. The deployed service reported `s390x` and returned risk scores that appeared in the local Xerxes audit records.

The old Project Idea Scheduler service on port 5000 was preserved during risk-service deployment. It is legacy VM state and is not part of Xerxes. The existing `notebook` Jupyter container on port 38888 was not stopped or modified.

## Windows desktop application

Build the distributable with:

```powershell
npm run desktop:dist
```

After a successful build, the current executable is copied to `application/Xerxes.exe`. Open that file to run the packaged desktop application. Close Xerxes before rebuilding so the replacement can be written. Electron owns the local Flask child and terminates its owned backend process tree during application shutdown; the native model is loaded inside that backend and is not left as a separate background service.

## Security boundaries and limitations

The initial agent tool set is intentionally narrow. `apply_patch`, arbitrary shell, deletion, network transfer, protected-path access, and privilege escalation are not enabled. The model can propose actions but cannot authorize them. The local policy engine and actual subprocess/file results are the source of truth.

Ollama `0.32.5` is installed on the verified development machine, but its local model inventory is currently empty. The local model runtime is therefore not ready and diagnostic planning is the honest default. A compact GGUF model through Ollama or llama.cpp can be configured after evaluating local disk/RAM and model output quality; no model is downloaded automatically. The LinuxONE risk model is trained on synthetic labels and must not be treated as a validated security predictor. No external data source is used.

## Attribution

React, Vite, Lucide React, Flask, Flask-CORS, Gunicorn, SQLite, pytest, NumPy, SciPy, and scikit-learn. References: [React](https://github.com/facebook/react), [Vite](https://github.com/vitejs/vite), [Flask](https://github.com/pallets/flask), [Gunicorn](https://github.com/benoitc/gunicorn), [scikit-learn](https://github.com/scikit-learn/scikit-learn), [NumPy](https://github.com/numpy/numpy), and [SciPy](https://github.com/scipy/scipy).
