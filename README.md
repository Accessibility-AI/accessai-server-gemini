# AccessAI Service

The AI service behind [AccessAI](https://github.com/Caculuz-cinks/accessai-android-sdk). It turns what a blind or
low-vision user says ("send 5k to David") into a structured suggestion from the host app's own list of allowed actions,
and it issues signed, single-use confirmation tokens for sensitive actions.

**It never executes anything and never touches money.** The host app's handlers and backend do that, after verifying
this service's token.

| Repo | What |
|---|---|
| [accessai-android-sdk](https://github.com/Caculuz-cinks/accessai-android-sdk) | Android SDK (built; this service's client) |
| **accessai-server** (this) | AI service |
| [accesspay-demo](https://github.com/Caculuz-cinks/accesspay-demo) | Demo fintech app + mock bank |

## The contract

**[contracts/v1/API_CONTRACT.md](contracts/v1/API_CONTRACT.md) is the source of truth.** The Android SDK already
implements the client side and has tests against it, so match it exactly (field names, error codes, canonical hash).
If you need to change it, change it in both repos in the same week and tell the SDK owner.

Endpoints: `POST /v1/sessions`, `POST /v1/interpret`, `POST /v1/confirmations`, `POST /v1/confirmations/{id}/confirm`,
`POST /v1/confirmations/{id}/cancel`, `GET /.well-known/jwks.json`.

## Suggested implementation

Python 3.12, FastAPI, Pydantic v2, Anthropic SDK (Claude Haiku for latency, with the model name in config), Ed25519
JWS for tokens, SQLite for sessions and pending confirmations, pytest.

```
app/
  main.py                  FastAPI app, routers, request-id middleware, error format
  api/                     sessions.py, interpret.py, confirmations.py, keys.py
  nlu/interpreter.py       provider → validate against the request's manifest → normalise → decide
  nlu/providers/claude.py  tool use: one tool per action in the request's manifest
  nlu/providers/rules.py   deterministic fallback (no API key needed for tests and local dev)
  nlu/amounts.py           "5k", "₦5,000", "five thousand naira" → kobo, deterministically
  nlu/confirm_grammar.py   strict confirm phrases for /confirm, no model
  security/signing.py      Ed25519 keys, JWS, canonical params SHA-256
evals/
  utterances.yaml          ~150 cases incl. Nigerian English ("abeg send 5k give David")
  run_evals.py             intent and slot accuracy, never-guess rate, latency
tests/
```

Non-negotiables (from the PRD):

- Only return `action_id`s from the request's `actions`. Never guess a recipient or an amount: omit it.
- Amounts are integers in kobo. Confirmation is never decided by a model.
- Keep API keys and the signing key on the server. Don't log raw text with amounts or names.
- `/interpret` must not execute anything.

The SDK's offline `LocalRulesBackend` (`accessai-core/.../net/LocalRulesBackend.kt`) is a small working reference for
the behaviour of every endpoint, minus the AI model and real signatures.

## CI

`.github/workflows/ci.yml` runs `ruff` and `pytest` on every push and pull request once a `pyproject.toml` or
`requirements.txt` exists.
