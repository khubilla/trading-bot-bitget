# Security Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 8 security vulnerabilities identified by the regression test suite so all tests in `tests/test_security.py` and `tests/test_security_scan.py` pass.

**Architecture:** All application fixes are in `dashboard.py` — new middleware stack at module level, handler-level validation in `get_candles` and `chat`. Dependency CVEs fixed by upgrading pinned versions.

**Tech Stack:** FastAPI, uvicorn, slowapi (new), starlette CORSMiddleware

**Branch:** `security/fix-vulnerabilities`

---

## Context

The test suite at `tests/test_security.py` was written TDD-style: tests are currently FAILING because vulnerabilities exist. Each task below fixes one vulnerability and makes the corresponding test class pass.

All tests in `test_security.py` already include `headers={"Authorization": "Bearer test-token"}` for post-fix compatibility. The conftest `live_server_url` fixture already sets `os.environ["DASHBOARD_API_KEY"] = "test-token"` before uvicorn starts.

After each task: run `pytest tests/test_security.py tests/test_security_scan.py -v` and confirm the target test class goes from FAIL to PASS.

---

## Task 1: Upgrade dependencies (fix 2 CVEs)

**Files:**
- Modify: `requirements.txt`

**CVEs to fix:**
- `cryptography` 46.0.5 → 46.0.6 (CVE-2026-34073)
- `requests` 2.32.5 → 2.33.0 (CVE-2026-25645)

- [ ] **Step 1: Upgrade packages**

```bash
pip install "cryptography==46.0.6" "requests==2.33.0"
```

- [ ] **Step 2: Update requirements.txt**

Change `requests>=2.31.0` to `requests>=2.33.0` and add `cryptography>=46.0.6`.

Final relevant lines in `requirements.txt`:
```
requests>=2.33.0
cryptography>=46.0.6
```

- [ ] **Step 3: Run scanner test**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_security_scan.py::test_no_dependency_cves -v
```
Expected: PASS (no CVEs found)

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "fix(deps): upgrade cryptography to 46.0.6 and requests to 2.33.0 (CVE fixes)"
```

---

## Task 2: Remove traceback leak from get_candles exception handler

**Files:**
- Modify: `dashboard.py` (line ~619-621)

**Current vulnerable code** (dashboard.py ~619-621):
```python
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()})
```

This leaks full Python stack traces including file paths to any caller.

- [ ] **Step 1: Write a failing test to confirm current behavior** (already exists — `TestInformationExposure`)

Verify it currently fails:
```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_security.py::TestInformationExposure -v
```
Expected: FAIL

- [ ] **Step 2: Fix the exception handler in dashboard.py**

Replace the 3-line except block at the end of `get_candles` (after line 617 `}`) with:

```python
    except Exception:
        return JSONResponse({"error": "internal server error"}, status_code=500)
```

The `except` block is the last lines of the `get_candles` function, at roughly lines 619-621.

- [ ] **Step 3: Run the test**

```bash
pytest tests/test_security.py::TestInformationExposure -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "fix(security): remove stack trace leak from get_candles exception handler"
```

---

## Task 3: Symbol allowlist regex on get_candles

**Files:**
- Modify: `dashboard.py` — `get_candles` function signature

**Spec:** Add regex allowlist `^[A-Z0-9]{2,20}$` to the `symbol` path parameter. Invalid symbols → 400.

- [ ] **Step 1: Verify test currently fails**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_security.py::TestInputValidation tests/test_security.py::TestInjectionAttacks -v
```
Expected: FAIL

- [ ] **Step 2: Add import and validator at top of get_candles**

Find the `get_candles` function definition:
```python
@app.get("/api/candles/{symbol}")
def get_candles(symbol: str, interval: str = "3m", limit: int = 80):
```

Add a `re` import at the top of `dashboard.py` (with the other stdlib imports on line 9), then add a validation block as the very first thing inside `get_candles`:

```python
import re
```

Add as the first lines inside the `get_candles` function body (before `try:`):
```python
    if not re.fullmatch(r"^[A-Z0-9]{2,20}$", symbol):
        return JSONResponse({"error": "invalid symbol"}, status_code=400)
```

- [ ] **Step 3: Run the tests**

```bash
pytest tests/test_security.py::TestInputValidation tests/test_security.py::TestInjectionAttacks -v
```
Expected: PASS for both classes

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "fix(security): add symbol allowlist regex to get_candles (blocks injection + path traversal)"
```

---

## Task 4: Payload size validation on /api/chat

**Files:**
- Modify: `dashboard.py` — `chat` function

**Spec:** Return 413 if `messages` field > 10,000 chars or `trade` field > 5,000 chars (serialized length).

- [ ] **Step 1: Verify test currently fails**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_security.py::TestAPIProxyAbuse -v
```
Expected: FAIL

- [ ] **Step 2: Add size check at start of chat handler**

The `chat` function currently starts with:
```python
@app.post("/api/chat")
async def chat(request: Request):
    """Stream a Claude trade analysis response via SSE."""
    import claude_analyst
    body      = await request.json()
    trade     = body.get("trade", {})
    messages  = body.get("messages", [])
```

After `body = await request.json()` and before the `trade`/`messages` lines, add:

```python
    import json as _json_size
    if len(_json_size.dumps(body.get("messages", []))) > 10_000:
        return JSONResponse({"error": "messages payload too large"}, status_code=413)
    if len(_json_size.dumps(body.get("trade", {}))) > 5_000:
        return JSONResponse({"error": "trade payload too large"}, status_code=413)
```

- [ ] **Step 3: Run the test**

```bash
pytest tests/test_security.py::TestAPIProxyAbuse -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "fix(security): add payload size validation to /api/chat (prevents Anthropic API abuse)"
```

---

## Task 5: CORSMiddleware restricted to localhost

**Files:**
- Modify: `dashboard.py` — add middleware import + CORSMiddleware after `app = FastAPI(...)` line

**Spec:** Add CORSMiddleware allowing only localhost/127.0.0.1 origins (any port, to support 8080, 8081, 8099). Evil external origins must NOT get `Access-Control-Allow-Origin`.

- [ ] **Step 1: Verify test currently fails**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_security.py::TestCORS -v
```
Expected: FAIL

- [ ] **Step 2: Add CORSMiddleware**

Add to imports at top of `dashboard.py`:
```python
from fastapi.middleware.cors import CORSMiddleware
```

After the `app = FastAPI(...)` line (line ~25), add:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
```

Using `allow_origin_regex` instead of `allow_origins` list covers all localhost ports (8080, 8081, 8099) without hardcoding.

- [ ] **Step 3: Run the test**

```bash
pytest tests/test_security.py::TestCORS -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "fix(security): add CORSMiddleware restricted to localhost origins"
```

---

## Task 6: Security headers response middleware

**Files:**
- Modify: `dashboard.py` — add `@app.middleware("http")` after CORSMiddleware

**Spec:** Every response must include:
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `Content-Security-Policy` — present, no `unsafe-inline` for script-src
- `Server` header — absent or not exposing uvicorn version

- [ ] **Step 1: Verify test currently fails**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_security.py::TestSecurityHeaders -v
```
Expected: FAIL

- [ ] **Step 2: Add security headers middleware**

After the `app.add_middleware(CORSMiddleware, ...)` block, add:

```python
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline'"
    )
    response.headers.pop("server", None)
    return response
```

- [ ] **Step 3: Run the test**

```bash
pytest tests/test_security.py::TestSecurityHeaders -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "fix(security): add security headers middleware (X-Frame-Options, CSP, nosniff, Referrer-Policy)"
```

---

## Task 7: Bearer token auth middleware (DASHBOARD_API_KEY)

**Files:**
- Modify: `dashboard.py` — add auth middleware

**Spec:** All endpoints return 401 if `Authorization: Bearer <token>` header is missing or token doesn't match `DASHBOARD_API_KEY` env var. If `DASHBOARD_API_KEY` is not set, auth is bypassed (so existing deployments without the env var still work).

- [ ] **Step 1: Verify test currently fails**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_security.py::TestAuthentication -v
```
Expected: FAIL (all 7 endpoints return 200 instead of 401)

- [ ] **Step 2: Add auth middleware**

After the security headers middleware, add:

```python
@app.middleware("http")
async def require_api_key(request: Request, call_next):
    api_key = os.environ.get("DASHBOARD_API_KEY", "")
    if api_key:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {api_key}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)
```

- [ ] **Step 3: Run the test**

```bash
pytest tests/test_security.py::TestAuthentication -v
```
Expected: PASS (all 7 endpoints return 401 without auth, 200 with correct Bearer token)

- [ ] **Step 4: Also run all test_security.py to confirm no regressions**

```bash
pytest tests/test_security.py -v
```
All previously passing tests should still pass (they all include `headers={"Authorization": "Bearer test-token"}`).

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "fix(security): add DASHBOARD_API_KEY bearer token auth middleware to all endpoints"
```

---

## Task 8: Rate limiting on /api/chat (slowapi)

**Files:**
- Modify: `dashboard.py` — add slowapi rate limiting
- Modify: `requirements.txt` — add slowapi
- Modify: `requirements-dev.txt` — no changes needed (slowapi is a runtime dep)

**Spec:** `/api/chat` returns 429 after 10 requests in 60 seconds from the same IP. Other endpoints are NOT rate-limited (trading data needs low latency).

- [ ] **Step 1: Install slowapi**

```bash
pip install "slowapi>=0.1.9"
```

- [ ] **Step 2: Add slowapi to requirements.txt**

Add: `slowapi>=0.1.9`

- [ ] **Step 3: Verify test currently fails**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_security.py::TestRateLimiting -v
```
Expected: FAIL

- [ ] **Step 4: Add slowapi to dashboard.py**

Add imports near the top of `dashboard.py` (with other imports):
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
```

After `app = FastAPI(...)` and before the middleware blocks, add:
```python
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

Decorate the `chat` function:
```python
@app.post("/api/chat")
@limiter.limit("10/minute")
async def chat(request: Request):
```

- [ ] **Step 5: Run the rate limit test**

```bash
pytest tests/test_security.py::TestRateLimiting -v
```
Expected: PASS

- [ ] **Step 6: Run full security test suite**

```bash
pytest tests/test_security.py tests/test_security_scan.py -v
```
Expected: ALL tests PASS (or only expected-to-fail ones remain)

- [ ] **Step 7: Commit**

```bash
git add dashboard.py requirements.txt
git commit -m "fix(security): add slowapi rate limiting to /api/chat (10 req/minute per IP)"
```

---

## Final: Push branch

```bash
git push -u origin security/fix-vulnerabilities
```
