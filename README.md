# AccessAI: Voice Intent Service with Google Gemini

A high-reliability, zero-guessing voice intent interpretation service designed for Android banking applications used by blind and low-vision users in Nigeria — powered by **Google Gemini** (`gemini-flash-lite-latest`, `gemini-2.0-flash`). Fully compliant with **AccessAI SDK 0.2.0 / API Contract v1.1** and **Security Specification v1.1**.

The service receives spoken (or typed) utterances across 8 languages (Nigerian English, Nigerian Pidgin, Yorùbá, Asụsụ Igbo, Harshen Hausa, French, German, and Spanish), matches them against the host application's action manifest using **Google Gemini function calling**, parses currency expressions deterministically into integer kobo (`amount_minor`), generates spoken prompts strictly via static templates, and mints short-lived cryptographically signed tokens (Ed25519 / RFC 8037 JWS) upon strict verbal or touch confirmation without LLM intervention.

---

## Performance & Latency (Live Gemini API)

Measured against live Google Gemini (`gemini-flash-lite-latest`):
- **p50 Latency:** ~747 ms
- **p95 Latency:** ~1,202 ms
- **Voice UX Budget:** < 1,500 ms (Comfortably achieved)
- **HTTP Transport:** Shared `httpx.AsyncClient` connection pool with HTTP Keep-Alive and 7.0s timeout.
- **Database:** SQLite in WAL mode (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`) for fast concurrent operations.

---

## Security Guarantees & Contract v1.1 Compliance

1. **Atomic Single-Use Confirmation Gate**:
   - High concurrency protection via atomic database update:
     `UPDATE confirmations SET status = 'confirmed' WHERE id = ? AND status = 'pending'`
   - Guarantees exactly one winner (no race condition / replay). All subsequent or concurrent attempts return `409 confirmation_used`.
2. **Strict Session & Device Authentication Binding**:
   - `session_id` is strictly verified and bound on confirmation. Unknown sessions reject with `401 session_expired`.
   - Cross-session hijacking is rejected with `404 not_found`.
   - Device key authentication (biometric / touch) is validated with Ed25519 signatures. Sensitive actions reject missing auth with `401 auth_invalid`.
3. **Data Privacy & Minimal JWS Token Claims**:
   - JWS tokens contain **no raw user parameters** (no recipient names or amounts).
   - Only standard Contract v1.1 claims: `iss: "accessai"`, `aid`, `ph` (canonical SHA-256 parameter hash), `jti`, `iat`, `exp`, and `amr`.
   - Canonical hash formatted strictly with `ensure_ascii=False` matching Android SDK byte-for-byte across UTF-8 accents (e.g. "Adébáyọ̀").
4. **Persistent Signing Keys**:
   - The service persists its Ed25519 signing key across restarts (`.ed25519_key` or `ED25519_PRIVATE_KEY_B64`), guaranteeing host backends can continuously verify tokens after redeployments.
5. **Zero Execution Rights & Zero Guessing**:
   - Service never transfers funds directly; it issues an authorized confirmation claim.
   - Ambiguous amounts or recipients fail over to deterministic clarification.
   - Confirmation phrases like *"yes"*, *"okay"*, or silence are rejected with `422 not_a_confirmation`. Only explicit confirmation (e.g. *"confirm"*, *"yes confirm"*) succeeds.

---

## API Endpoints (Contract v1.1)

| Method | Endpoint | Status | Description |
|---|---|---|---|
| `POST` | `/v1/sessions` | `200 OK` | Create or retrieve dialog session (`{session_id, expires_at}`) |
| `POST` | `/v1/interpret` | `200 OK` | Interpret utterance against action manifest (`text`, `actions`, `dialog`) |
| `POST` | `/v1/confirmations` | `200 OK` | Register pending confirmation (`{pending_id, params_sha256, expires_at}`) |
| `POST` | `/v1/confirmations/{id}/confirm` | `200 OK` | Confirm action via voice phrase or device auth; mints signed token |
| `POST` | `/v1/confirmations/{id}/cancel` | `204 No Content` | Idempotently cancel pending confirmation |
| `GET` | `/.well-known/jwks.json` | `200 OK` | Serve Ed25519 public key in standard JWKS format |
| `GET` | `/health` | `200 OK` | Health check reporting provider, DB, and signing key status |

---

## Supported Currency & Dialect Expressions

The deterministic parser converts Nigerian English, Pidgin, and standard expressions into integer **kobo** (1 Naira = 100 kobo):
- `"5k"`, `"5 grand"`, `"5000 naira"`, `"N5000"`, `"₦5,000"` $\rightarrow$ `500,000` kobo (₦5,000)
- `"1.5k"` $\rightarrow$ `150,000` kobo (₦1,500)
- `"20k"`, `"twenty thousand naira"` $\rightarrow$ `2,000,000` kobo (₦20,000)
- `"2m"`, `"2m naira"` $\rightarrow$ `200,000,000` kobo (₦2,000,000)
- `"half a million naira"` $\rightarrow$ `50,000,000` kobo (₦500,000)
- `"500 naira 50 kobo"` $\rightarrow$ `50,050` kobo (₦500.50)
- `"50 kobo"` $\rightarrow$ `50` kobo (₦0.50)

---

## Setup & Running Locally

### 1. Setup Virtual Environment

**Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows:**
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Set your Google Gemini API key:
```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-flash-lite-latest
```

### 3. Run the Server

**Linux / macOS:**
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Windows:**
```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# or using the virtual environment directly:
.venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Testing & Verification

### Run Full Test Suite (59 Tests)

Includes unit tests, contract v1.1 conformance tests, concurrent single-use stress tests, device authentication checks, and privacy tests:

```bash
pytest -v
```

### Run Benchmark Evaluator (150 Utterances Across 8 Languages)

**Using Mock Provider (Deterministic baseline):**
```bash
python tests/evals/run_evals.py --provider mock
```

**Using Live Google Gemini:**
```bash
python tests/evals/run_evals.py --provider gemini --limit 20
```
*(Measures accuracy, zero-guessing compliance, and p50/p90/p95/p99 latency distribution)*.

---

## Deploying to Fly.io

`fly.toml` runs **one** always-on machine in London (`lhr`), with SQLite on a persistent volume mounted at `/data`. In production the server refuses to start without `GEMINI_API_KEY` and `ED25519_PRIVATE_KEY_B64`.

**First deploy:**

```bash
flyctl apps create accessai-server-gemini
flyctl volumes create accessai_data --region lhr --size 1

# Generate the token-signing key locally (never commit it or paste it anywhere)
python -c "import base64; from cryptography.hazmat.primitives.asymmetric import ed25519; from cryptography.hazmat.primitives import serialization as s; print(base64.b64encode(ed25519.Ed25519PrivateKey.generate().private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption())).decode())"

flyctl secrets set GEMINI_API_KEY=<key> ED25519_PRIVATE_KEY_B64=<key-from-above> --stage
flyctl deploy --ha=false
```

**Check it:** `https://accessai-server-gemini.fly.dev/health` and `/.well-known/jwks.json`.

**Continuous deployment:** after the first deploy, every push or merge to `main` deploys automatically. The `deploy` job in `.github/workflows/ci.yml` runs after lint and tests pass, then smoke-checks `/health` and the JWKS. PRs and other branches never deploy. Deploys appear under the repo's **production** environment.

One-time setup, done by a **repo admin**: create a deploy token scoped to this app and store it as the `FLY_API_TOKEN` Actions secret. Piping it means the token is never printed:

```bash
flyctl tokens create deploy -a accessai-server-gemini -x 8760h | gh secret set FLY_API_TOKEN -R Accessibility-AI/accessai-server-gemini
```

Until the secret exists, the deploy job skips with a notice instead of failing. The token expires after a year (`-x 8760h`), so repeat this when it does.

**Notes:**
- Keep it to one machine (`--ha=false`). Each machine would have its own SQLite file, so a confirmation started on one couldn't be completed on another.
- When rotating the signing key, also change `JWKS_KID` in `fly.toml` so bank backends can tell the keys apart.
- The service has no rate limiting yet, so set a spending cap on the Gemini API key before sharing the URL.

---

## License

Apache-2.0
