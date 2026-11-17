# VeriPact QA results

Validated on November 15, 2026 with Python 3.12.14 on Windows and approximately 7.7 GB usable RAM.

## Passed

- `python test_suite.py`: **59 passed**, with Python warnings treated as errors (including the performance and open-source release follow-ups).
- `python -m pip check`: no broken requirements.
- Previously verified `streamlit run app.py --server.headless true`: clean Uvicorn startup on `127.0.0.1:8501`. The free application flow is verified by the current Streamlit AppTest suite.
- Streamlit AppTest: direct workspace access without activation, reviewer and optional firm input, sample loading, completed review, audit metadata, Word report creation and clearing the session. Missing, damaged or expired-looking legacy state does not block access and is not modified; licensing imports are explicitly rejected during the access test.
- Standalone legacy utility tests: Ed25519 signatures, expiration, tampered keys, clock rollback, trial boundaries, state corruption and the CLI entrypoint. These utilities are not part of the application's import or execution path. The CLI test invokes its real argparse entrypoint in-process to avoid an unnecessary extra interpreter on low-memory machines.
- PDF, DOCX and strict UTF-8 extraction; 35-page chunk coverage; exact and fuzzy quote verification; rejection of changed numbers and negations; malformed JSON and schema handling.
- Concurrent and idempotent metadata logging, incomplete-review handling, report contents and color-coded Word tables.
- Multi-megabyte multipart upload with disk rollover forbidden by the test; local-only model endpoint with environment proxies and remote host overrides ignored.
- Git ignore verification: environment/cache paths, activation state, audit logs and nested DOCX outputs are excluded; README, LICENSE, app.py and Streamlit configuration remain includable.

## Open-source release

The application is free under the MIT License, with no account, activation, trial or paid feature gate. Firm metadata comes from an optional sidebar field. `license_manager.py` and `generate_license.py` remain independent legacy reference utilities; the application does not import them or touch their state. Existing standalone regression coverage is retained.

## Real model observations and limitations

An installed `qwen3:4b` was exercised on the synthetic NDA. Early runs returned inconsistent fact labels and explicit empty placeholders, timed out on one segment, and produced an unsupported liability-cap interpretation. Valid risk quotes were matched to the original source. The application correctly flagged incomplete processing.

These observations initially led to field normalization, explicit absent-value handling, validation feedback on retries, clearer numerical playbook instructions, a longer inference timeout and smaller inference batches. That initial configuration used a 4,096-token context, a 768-token response cap and byte-based context budgeting. The performance follow-up below supersedes its chunking and output settings.

## Performance follow-up

Safe Mode now allows **4,500-character segments**, retains the **4,096-token context**, and uses **1,024 output tokens** with `think=False`. Prompt planning estimates English tokens, and short documents are combined across page boundaries without changing stored source pages. The included NDA is verified to produce **one 4,449-character segment and two normal model calls** using the deterministic integration fixture. Both page markers and all normalized source content are preserved. Whole-document reviews avoid redundant glossary batches because every source definition is already present.

Regression tests cover legacy SDK and daemon rejection of the `think` parameter, the `/no_think` prompt fallback, the output cap, unrelated errors that must not be retried, and context budgeting for longer non-ASCII sources. These tests validate the call structure and compatibility behavior; they do **not** establish a real-model runtime below 30 seconds.

The final full real-model run could not be certified on this workstation: less than approximately 1 GB of RAM remained free after unloading the model, and the user had closed the applications they could. Earlier runs are retained as synthetic QA evidence under `.qa/`; they are **not passing acceptance results**. Run `python live_smoke.py --model qwen3:4b` after sufficient memory is available. Its exit code is zero only when both stages complete, all three planted trap categories appear, and every risk quote passes source verification. Legal interpretations still require attorney review even when that check passes.

Visual browser verification was unavailable because the in-app browser service timed out and Computer Use did not have approval to access Chrome. Word access was also not approved, and the packaged DOCX renderer could not find LibreOffice. DOCX structure/content checks pass, but **rendered page layout is not certified**. The generated QA reports are not final client deliverables.

The software does not certify a physical air gap, forensic memory erasure or exhaustive legal accuracy. See README.md for concrete operational boundaries and deployment guidance.
