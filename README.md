# AccessAI: Voice Intent Service with Google Gemini

A high-reliability, zero-guessing voice intent interpretation service designed for Android banking applications used by blind and low-vision users in Nigeria — powered by **Google Gemini** function calling (`gemini-1.5-flash`, `gemini-2.0-flash`, `gemini-1.5-pro`).

The service receives spoken (or typed) utterances across 8 languages (Nigerian English, Nigerian Pidgin, Yorùbá, Asụsụ Igbo, Harshen Hausa, French, German, and Spanish), matches them against the host application's action manifest using **Google Gemini function calling**, parses currency expressions deterministically into integer kobo, generates spoken prompts strictly via static templates, and mints short-lived cryptographically signed tokens (Ed25519 / RFC 8037 JWS) upon strict verbal confirmation without LLM intervention.

---

## Key Security & Architectural Guarantees

1. **Zero Execution Rights**: The service never moves money or executes any banking action. It only provides structured interpretations. The Android app and host backend execute actions only after user confirmation.
2. **Zero Guessing on Money & Beneficiaries**:
   - Transfers missing a recipient or amount immediately trigger a deterministic clarification question.
   - Raw amount phrases (e.g., `"5k"`, `"twenty thousand naira"`, `"N5000"`) are parsed into integer kobo deterministically. If the LLM's estimate disagrees with the deterministic parser, it fails over to clarification.
3. **No PII or Balances Sent to Google Gemini**:
   - The LLM only receives the sanitized utterance, the action manifest, the screen descriptor, and dialog history.
   - User account balances, beneficiary lists, PINs, and BVNs are strictly excluded.
4. **Deterministic Spoken Prompts**:
   - Spoken text for confirmations and amount summaries is generated purely by code templates, never by the LLM. Hallucinated numbers cannot slip through into audio readouts.
5. **Strict Confirmation (No LLM Involved)**:
   - Phrases like *"yes"*, *"okay"*, *"sure"*, or silence are explicitly rejected.
   - Only exact phrases (e.g., `"confirm"`, `"i confirm"`) trigger token issuance.
6. **Short-Lived Signed Tokens (Ed25519 / JWS)**:
   - On confirmation, a JWS token (~60s TTL) is minted, bound to:
     - `action` (e.g. `transfer_money`)
     - `params_hash` (SHA-256 of canonical JSON params)
     - `sub` (session ID)
     - `nonce` (single-use nonce)
     - `exp` (timestamp)
   - Public keys are served at `GET /.well-known/jwks.json` for backend verification.

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/v1/sessions` | Create or initialize a voice dialog session |
| `POST` | `/v1/interpret` | Interpret utterance against app action manifest using Google Gemini |
| `POST` | `/v1/confirmations/pending` | Register a pending action before confirmation |
| `POST` | `/v1/confirmations/{id}/confirm` | Check confirmation phrase & mint signed JWS token |
| `POST` | `/v1/confirmations/{id}/cancel` | Cancel a pending confirmation |
| `GET` | `/.well-known/jwks.json` | Serve Ed25519 public key in standard JWKS format |
| `GET` | `/health` | Healthcheck endpoint reporting active provider |

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

## Running Locally with Google Gemini

### 1. Setup Virtual Environment

```bash
# Using uv (fastest)
uv venv .venv --python 3.12
.venv\Scripts\activate   # Windows
# or: source .venv/bin/activate (Linux/macOS)
uv pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Set `LLM_PROVIDER=gemini` and supply your `GEMINI_API_KEY`:

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-1.5-flash
```

*(You can also use `GEMINI_MODEL=gemini-2.0-flash` or `gemini-1.5-pro`)*

### 3. Run the Service

```bash
.venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Running Tests & 150-Utterance Evals

### Run Full Test Suite (33 Unit & Integration Tests)

```bash
.venv\Scripts\pytest -v
```

### Run Benchmark Evaluator (150 Multilingual Utterances)

```bash
.venv\Scripts\python tests/evals/run_evals.py
```

---

## Provider Comparison: Gemini vs Groq vs Anthropic

| Feature | Google Gemini (This folder) | Groq Llama 3.3 (`accessai-server`) | Anthropic Claude 3.5 |
|---|---|---|---|
| **Default Model** | `gemini-1.5-flash` / `gemini-2.0-flash` | `llama-3.3-70b-versatile` | `claude-3-5-haiku-20241022` |
| **Tool Calling Format** | Gemini OpenAPI Function Declarations | OpenAI Function Tool Schema | Anthropic Input Schema |
| **Multilingual Dialect Parsing** | Supported (8 languages) | Supported (8 languages) | Supported (8 languages) |
| **Deterministic Parsing Gate** | Integrated | Integrated | Integrated |
| **Offline Test Mock Mode** | Supported (`LLM_PROVIDER=mock`) | Supported (`LLM_PROVIDER=mock`) | Supported (`LLM_PROVIDER=mock`) |
