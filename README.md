# VeriPact AI

### Air-Gapped Contract Auditor

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Ollama](https://img.shields.io/badge/Inference-Ollama-1E293B)](https://ollama.com/)
[![100% Air-Gapped](https://img.shields.io/badge/Air--Gapped-100%25-0F172A)](#privacy-and-air-gapped-operation)
[![MIT License](https://img.shields.io/badge/License-MIT-D97706)](LICENSE)

**An open-source contract review workspace that keeps client documents on your hardware.** VeriPact AI helps attorneys identify playbook deviations, inspect the language behind each finding, and prepare an executive Word report using local Ollama inference.

The full application is free under the **MIT License**. No account, subscription, activation key or trial is required. Install the dependencies and a local model, then launch the auditor.

> **Air-gapped operation:** download dependencies and models during setup, then disconnect the review machine from external networks. The app uses loopback connections only. The badge describes this deployment mode; software alone cannot establish a physical air gap.

## Built for evidence-led review

| Capability | What you get |
| --- | --- |
| Document intake | PDF, DOCX and UTF-8 TXT extraction with page references |
| Fact sheet | Parties, effective date, term, renewal window, governing law and liability cap |
| Definitions trap detection | Review of defined terms that can change the meaning of obligations elsewhere |
| Firm playbook | Editable rules applied to each agreement |
| Source verification | Quote matching with verified or manual-check labels |
| Negotiation output | Severity, supporting quote, trap explanation and suggested redline |
| Executive Word report | Firm/reviewer metadata, SHA-256 fingerprint, facts, glossary and color-coded risks |
| Local audit trail | Completed-review metadata, model identity and risk counts |

## Two-Stage Anti-Hallucination Audit

### Stage 1 — Facts and defined terms

The first pass extracts the agreement's key facts and a glossary of capitalized defined terms. The original source remains available for inspection, and each extracted quote is checked independently against it.

### Stage 2 — Playbook deviations and definitions traps

The second pass compares contract language with the attorney's playbook and resolves definitions that alter obligations. For example, a clause may say that the **Indemnifying Party** protects the **Protected Parties**, while an earlier definition assigns those roles to only one side. An apparently reciprocal agreement can therefore contain a unilateral indemnity.

Short documents are reviewed together so all definitions remain in the same source segment. Longer agreements use overlapping segments and cross-page definition context. Interactions that span separate context batches are flagged for manual review.

### Independent evidence checks

Every fact, definition and risk quote is normalized and compared with the source using `difflib.SequenceMatcher` at a **0.85 threshold**. Exact matches are distinguished from approximate matches; changed numbers and negations cannot pass a fuzzy match. Verified matches use the located source page rather than blindly trusting a model-generated citation.

Structured JSON, schema validation and one validation retry keep malformed responses from appearing as successful reviews. Unrecoverable failures are marked **incomplete** and are not logged as completed audits.

**Source verification checks wording, not legal correctness.** It cannot eliminate hallucinated interpretations or prove that every risk was found. All findings and redlines require independent attorney review.

The default playbook is an editable example:

- Governing law: Delaware, New York or Minnesota.
- Aggregate liability: no more than 12 months of fees.
- Auto-renewal: at least 60 days' notice of non-renewal.
- Indemnification: mutual obligations only.

## Quickstart — 3 steps

Prerequisites: **Python 3.12+**, [Ollama](https://ollama.com/), and enough available memory for the selected model. The automated suite has been validated on Windows with Python 3.12. Perform package installation and model downloads before disconnecting the machine.

### 1. Get the source and install dependencies

Clone this repository or download its ZIP, then open a terminal in the project directory.

```sh
python -m venv .venv
```

**Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**macOS / Linux:**

```sh
.venv/bin/python -m pip install -r requirements.txt
```

Use `requirements-lock.txt` instead for the exact dependency versions recorded during validation.

### 2. Start local Ollama and install a model

If Ollama is already running, quit or stop that instance before starting the daemon with cloud features disabled. Keep this terminal running.

**Windows PowerShell:**

```powershell
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_HOST = '127.0.0.1:11434'
ollama serve
```

**macOS / Linux:**

```sh
OLLAMA_NO_CLOUD=1 OLLAMA_HOST=127.0.0.1:11434 ollama serve
```

In a **second terminal**, download the recommended starting model while connected:

```sh
ollama pull qwen3:4b
```

A pre-existing service must be restarted with the new environment; setting it in VeriPact cannot reconfigure a running daemon. See [Ollama's local configuration guide](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features).

### 3. Launch the free auditor

From the project directory in the second terminal:

**Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

**macOS / Linux:**

```sh
.venv/bin/python -m streamlit run app.py
```

Open **[127.0.0.1:8501](http://127.0.0.1:8501)**. Enter a reviewer name, optionally add your firm name, load the sample NDA or upload an agreement, and select **Run two-stage audit**. The complete workspace opens immediately.

The fictional two-page sample includes Texas governing law, a definition-driven unilateral indemnity, and a 15-day renewal notice. It is intended for demonstrations and QA, not use as a contract template.

## Hardware and performance

| System RAM | Suggested local model | Review settings |
| --- | --- | --- |
| Under 12 GB | `qwen3:4b` | Safe Mode: up to 4,500 characters per segment; 4K context |
| 12–24 GB | `qwen3:8b` or `qwen3:14b` | Balanced mode; check available memory before choosing 14B |
| Over 24 GB | `qwen3.8:27b` or `gemma4:31b` | Larger models where available RAM / GPU capacity permits |

All chat calls request `think=False` and cap output at **1,024 tokens**. Older SDKs or daemons that explicitly reject the parameter receive a compatibility retry with the best-effort `/no_think` prompt instruction. The included NDA fits one source segment and normally takes two model calls, one per stage.

Prompt budgets use token estimates, not the selected model's exact tokenizer. Longer or non-ASCII sources can require smaller segments. Actual speed and model fit depend on free memory, quantization, processor and GPU capacity; a sub-30-second review has **not** been established by a live benchmark. See [QA results and verification limits](QA_RESULTS.md).

## Privacy and air-gapped operation

- **Local connections:** Streamlit binds to `127.0.0.1`; the Ollama client is pinned to `127.0.0.1:11434`. Cloud model names, remote manifests and empty model stubs are rejected. Proxy inheritance and redirects are disabled for inference.
- **Document content:** uploads, extracted text, findings and report bytes stay in session memory. Multipart disk spooling is disabled before the upload widget is created. The app does not save contract bodies or model responses to disk.
- **Intentional retention:** `audit_log.json` stores review metadata, including filename, reviewer, SHA-256, model and risk counts. Downloaded reports are saved by the user. Set `VERIPACT_DATA_DIR` to choose a protected local metadata directory.
- **Session lifecycle:** use **Clear document & review** to remove the app's session references. Streamlit can retain disconnected sessions briefly. This is not a promise of forensic memory erasure, and operating-system swap, crash dumps, backups and external Ollama diagnostics require separate controls.
- **Connected setup:** model downloads require connectivity. Contract review is disabled while the optional connected-setup control is enabled. The README badges are hosted images for GitHub; the running app does not use them or other remote visual assets.

For installation on an isolated target, prepare a wheelhouse on a connected machine with the same operating system, architecture and Python version, then transfer it and the Ollama installation/model files through your approved offline process:

```sh
python -m pip download -r requirements-lock.txt -d wheelhouse
python -m pip install --no-index --find-links=wheelhouse -r requirements-lock.txt
```

## Document scope

PDF references use original pages. TXT uses consecutive `[Page X]` markers or form-feed sections. DOCX uses explicit page-break sections; export to PDF when exact rendered Word page numbers matter. DOCX headers, footers, text boxes and tracked deletions are outside extraction scope. Scanned PDFs require local OCR before review.

Limits are **25 MB**, **500 page sections** and **2 million extracted characters** per document. Empty or unreadable pages are flagged. Prompt isolation reduces instruction-injection risk but cannot guarantee immunity.

## Development and testing

Use your virtual environment's Python executable; for example on Windows:

```powershell
.\.venv\Scripts\python.exe test_suite.py
.\.venv\Scripts\python.exe -m pip check
```

The automated suite requires no live model or browser clicks. It covers extraction, source matching, malformed JSON, context planning, Ollama compatibility, upload privacy, report creation, metadata logging and direct access to the free Streamlit workspace, including installations with stale legacy license state.

To exercise the actual local model with the synthetic NDA:

```powershell
.\.venv\Scripts\python.exe live_smoke.py --model qwen3:4b
```

This optional QA utility writes synthetic results into the ignored `.qa/` directory. It is separate from the app's in-memory handling of client documents.

| Module | Responsibility |
| --- | --- |
| `app.py` | Free Streamlit workspace, review controls and downloads |
| `document_engine.py` | In-memory extraction, page-aware chunking and quote verification |
| `audit_engine.py` | Two-stage review, schema validation and audit metadata |
| `model_gateway.py` | Hardware profiles, local Ollama calls and compatibility handling |
| `report_builder.py` | Executive Word reports generated in memory |
| `privacy.py` / `storage.py` | Upload transport controls and atomic metadata writes |
| `test_suite.py` / `live_smoke.py` | Automated regression tests and optional real-model QA |

`license_manager.py` and `generate_license.py` remain standalone legacy reference utilities with independent tests. **The auditor does not import or use either module**, does not read or create activation state, and has no paid feature gate.

Contributions are welcome. Include a focused regression test for behavior changes, run the suite, and use synthetic documents when reporting issues. Keep client contracts, audit metadata, signing keys and generated reports out of commits; the supplied `.gitignore` excludes local environments, caches, state, logs and Word outputs.

## License

VeriPact AI is released under the [MIT License](LICENSE), copyright 2026 VeriPact AI contributors. Third-party libraries and model weights retain their respective licenses.

## Legal Disclaimer

Disclaimer: VeriPact AI is an AI-assisted document extraction and review utility for licensed attorneys. It does not constitute legal advice. All outputs must be independently verified by counsel.
