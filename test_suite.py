"""Run with python test_suite.py. Offline tests never require a live model."""
from __future__ import annotations

import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from docx import Document
from reportlab.pdfgen.canvas import Canvas

from audit_engine import DEFAULT_PLAYBOOK, AuditResult, ResponseError, append_audit_log, audit_document, estimate_prompt_tokens, parse_json_response, plan_audit_chunks, validate_response
from document_engine import DocumentError, Page, SourceDocument, chunk_document, parse_document, verify_quote
from generate_license import generate_license
from license_manager import LicenseError, LicenseManager, verify_license
from model_gateway import ModelError, chat_without_thinking, download_model, ensure_local_model, hardware_profile, list_installed_models
from report_builder import build_report

ROOT = Path(__file__).resolve().parent
NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


@pytest.fixture
def signing_key():
    return Ed25519PrivateKey.generate()


def test_license_signature_and_expiry(signing_key):
    key = generate_license(signing_key, "Example & Partners", "2026-11-15")
    public = signing_key.public_key().public_bytes_raw()
    assert verify_license(key, public, NOW)["firm"] == "Example & Partners"
    assert verify_license(key, public, NOW.replace(hour=23, minute=59, second=59))
    with pytest.raises(LicenseError, match="expired"):
        verify_license(key, public, NOW + timedelta(days=1))
    with pytest.raises(LicenseError):
        verify_license(key, Ed25519PrivateKey.generate().public_key().public_bytes_raw(), NOW)


def test_license_tampered_payload(signing_key):
    from license_manager import decode, encode
    key = generate_license(signing_key, "Firm A", "2027-01-01")
    prefix, payload, signature = key.split(".")
    changed = decode(payload).replace(b"Firm A", b"Firm B")
    with pytest.raises(LicenseError):
        verify_license(f"{prefix}.{encode(changed)}.{signature}", signing_key.public_key().public_bytes_raw(), NOW)


def test_founder_cli_roundtrip_and_private_key_overwrite_protection(tmp_path, monkeypatch):
    import base64
    import re
    import runpy
    import shutil
    from contextlib import redirect_stdout
    for name in ("generate_license.py", "license_manager.py", "storage.py"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    # Exercise the real argparse/__main__ entrypoint in this process. Spawning
    # another interpreter is unnecessary and costly on low-memory laptops.
    monkeypatch.chdir(tmp_path)
    def invoke(*arguments):
        monkeypatch.setattr(sys, "argv", ["generate_license.py", *arguments])
        output = io.StringIO()
        with redirect_stdout(output):
            runpy.run_path(str(tmp_path / "generate_license.py"), run_name="__main__")
        return output.getvalue()
    command = ("keygen", "--private-key", "founder.pem", "--install-public-key")
    generated = invoke(*command)
    assert "PRIVATE KEY" not in generated
    public_text = re.search(r'PUBLIC_KEY_B64 = "([^"]+)"', (tmp_path / "license_manager.py").read_text()).group(1)
    issued = invoke("issue", "--private-key", "founder.pem", "--firm", "QA Firm", "--expires", "2027-01-01")
    assert verify_license(issued.strip(), base64.b64decode(public_text), NOW)["firm"] == "QA Firm"
    with pytest.raises(SystemExit) as rejected:
        invoke(*command)
    assert rejected.value.code == 2


@pytest.mark.parametrize("key", ["", "VP1.bad.bad", "a.b.c.d", "VP2.x.y", "X" * 9000])
def test_invalid_license_formats(key):
    with pytest.raises(LicenseError):
        verify_license(key, now=NOW)


def test_trial_boundaries_and_no_restart(tmp_path):
    manager = LicenseManager(tmp_path / ".vp_state.dat")
    assert not manager.status(NOW).active
    manager.start_trial(NOW)
    assert manager.status(NOW + timedelta(days=7, microseconds=-1)).active
    assert not manager.status(NOW + timedelta(days=7)).active
    with pytest.raises(LicenseError, match="already"):
        manager.start_trial(NOW + timedelta(days=8))


def test_clock_rollback_persists(tmp_path):
    path = tmp_path / ".vp_state.dat"
    manager = LicenseManager(path)
    manager.start_trial(NOW)
    assert manager.status(NOW + timedelta(hours=1)).active
    status = manager.status(NOW + timedelta(minutes=59))
    assert not status.active and "rollback" in status.message
    assert not LicenseManager(path).status(NOW + timedelta(hours=2)).active


def test_state_tampering_fails_closed(tmp_path):
    path = tmp_path / ".vp_state.dat"
    manager = LicenseManager(path)
    manager.start_trial(NOW)
    packet = json.loads(path.read_text())
    packet["state"]["trial_started"] = (NOW + timedelta(days=100)).isoformat()
    path.write_text(json.dumps(packet))
    assert not manager.status(NOW).active
    with pytest.raises(LicenseError):
        manager.start_trial(NOW)


def test_activation_and_renewal(tmp_path, signing_key):
    manager = LicenseManager(tmp_path / ".vp_state.dat", signing_key.public_key().public_bytes_raw())
    manager.activate(generate_license(signing_key, "Firm", "2026-11-15"), NOW)
    assert manager.status(NOW).mode == "Licensed"
    assert not manager.status(NOW + timedelta(days=1)).active
    manager.activate(generate_license(signing_key, "Firm", "2027-11-15"), NOW + timedelta(days=1))
    assert manager.status(NOW + timedelta(days=1)).active


def test_utf8_and_page_markers():
    source = parse_document((ROOT / "sample_test_nda.txt").read_bytes(), "sample_test_nda.txt")
    assert len(source.pages) == 2 and "Section 7" in source.pages[1].text
    assert len(source.sha256) == 64 and "[Page 2]" in source.text
    assert len(parse_document(b"Alpha\fBeta", "x.txt").pages) == 2
    assert parse_document(b"\xef\xbb\xbfHello", "x.txt").pages[0].text == "Hello"
    with pytest.raises(DocumentError, match="UTF-8"):
        parse_document(b"\xff\xfeinvalid", "x.txt")


@pytest.mark.parametrize("text", [b"[Page 2]\nText", b"[Page 1]\nA\n[Page 1]\nB", b"Prefix\n[Page 1]\nText"])
def test_ambiguous_text_pagination_rejected(text):
    with pytest.raises(DocumentError):
        parse_document(text, "x.txt")


def make_pdf(blank=False):
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    if not blank:
        canvas.drawString(72, 720, "Governing law is Texas.")
    canvas.showPage()
    if not blank:
        canvas.drawString(72, 720, "Renewal requires 15 days notice.")
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def test_pdf_extraction():
    source = parse_document(make_pdf(), "contract.pdf")
    assert len(source.pages) == 2
    assert "Texas" in source.pages[0].text
    assert "15 days" in source.pages[1].text


def test_scan_and_corrupt_pdf_rejected():
    with pytest.raises(DocumentError, match="OCR"):
        parse_document(make_pdf(blank=True), "scan.pdf")
    with pytest.raises(DocumentError):
        parse_document(b"bad", "contract.pdf")


def test_docx_tables_and_explicit_breaks():
    doc = Document()
    doc.add_paragraph("First page")
    doc.add_table(rows=1, cols=1).cell(0, 0).text = "Table evidence"
    doc.add_page_break()
    doc.add_paragraph("Second page")
    buffer = io.BytesIO()
    doc.save(buffer)
    source = parse_document(buffer.getvalue(), "contract.docx")
    assert len(source.pages) == 2
    assert "Table evidence" in source.pages[0].text
    assert "Second page" in source.pages[1].text
    assert "explicit" in source.pagination


@pytest.mark.parametrize("raw,name", [(b"", "x.txt"), (b"   ", "x.txt"), (b"hello", "x.exe"), (b"a\x00b", "x.txt")])
def test_invalid_documents(raw, name):
    with pytest.raises(DocumentError):
        parse_document(raw, name)


def test_thirty_page_chunking_has_no_loss():
    pages = [Page(i, " ".join(f"page{i}token{j}" for j in range(600))) for i in range(1, 36)]
    source = SourceDocument("long.txt", "a" * 64, pages)
    chunks = chunk_document(source, 3500)
    assert len(chunks) > 35 and max(map(lambda c: len(c.text), chunks)) <= 3500
    combined = "\n".join(c.text for c in chunks)
    for page in pages:
        assert f"[Page {page.number}]" in combined
        assert all(token in combined for token in page.text.split())
    assert any(len(chunk.pages) == 2 for chunk in chunks)


def test_quote_exact_unicode_and_correct_page():
    pages = [Page(1, "Unrelated"), Page(2, "The receiving Party’s obligation\nshall survive for three years.")]
    evidence = verify_quote("The receiving Party's obligation shall survive for three years.", pages)
    assert evidence.verified and evidence.page == 2 and evidence.exact


def test_quote_fuzzy_and_hallucination():
    pages = [Page(3, "The receiving party shall protect all confidential information using reasonable care.")]
    assert verify_quote("The receiving party shall protect all confidential informations using reasonable care.", pages).verified
    assert not verify_quote("The vendor shall pay ten million dollars.", pages).verified
    assert not verify_quote("", pages).verified


def test_quote_numeric_or_negation_changes_fail():
    pages = [Page(1, "Either Party must give notice at least 15 days before the expiration date.")]
    assert not verify_quote("Either Party must give notice at least 60 days before the expiration date.", pages).verified
    assert not verify_quote("Either Party must not give notice at least 15 days before the expiration date.", pages).verified


@pytest.mark.parametrize("text", ['{"risks": []}', '```json\n{"risks": []}\n```', 'Here is the answer: {"risks": []} Done.', '<think>private thinking</think>{"risks": []}'])
def test_json_fallback(text):
    assert parse_json_response(text) == {"risks": []}


def test_json_nested_braces_and_schema_failures():
    assert parse_json_response('prose {"a": {"text": "brace } in string"}} end')["a"]["text"] == "brace } in string"
    with pytest.raises(ResponseError):
        parse_json_response("not JSON")
    for data in ({}, {"risks": None}, {"risks": [{"severity": "CRITICAL"}]}):
        with pytest.raises(ResponseError):
            validate_response(data, 2)


def test_model_field_normalization_and_absent_placeholders():
    data = {"facts": [{"field": "governing_law", "value": "Texas", "quote": "Laws of Texas", "page": "2"}, {"field": "Term", "value": "Not found", "quote": "", "page": 1}], "glossary": []}
    parsed = validate_response(data, 1)
    assert len(parsed["facts"]) == 1
    assert parsed["facts"][0]["field"] == "Governing Law"
    assert parsed["facts"][0]["page"] == 2
    with pytest.raises(ResponseError):
        validate_response({"facts": [{"field": "Term", "value": "one year", "quote": "", "page": 1}], "glossary": []}, 1)


def test_upload_transport_never_spools_to_disk(monkeypatch):
    import asyncio
    import tempfile
    from starlette.requests import Request
    from privacy import configure_memory_only_uploads
    def refuse_disk(self):
        raise AssertionError("Contract upload attempted disk rollover")
    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", refuse_disk)
    configure_memory_only_uploads()
    payload = b"contract evidence " * 180000  # Exceeds the default 1 MB spool limit.
    body = b'--qa\r\nContent-Disposition: form-data; name="file"; filename="contract.txt"\r\nContent-Type: text/plain\r\n\r\n' + payload + b'\r\n--qa--\r\n'
    async def exercise():
        remaining = body
        async def receive():
            nonlocal remaining
            chunk, remaining = remaining[:65536], remaining[65536:]
            return {"type": "http.request", "body": chunk, "more_body": bool(remaining)}
        request = Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"content-type", b"multipart/form-data; boundary=qa")]}, receive)
        async with request.form() as form:
            upload = form["file"]
            assert not upload.file._rolled
            assert await upload.read() == payload
    asyncio.run(exercise())


def test_local_client_ignores_remote_host_and_proxy(monkeypatch):
    from model_gateway import local_client
    captured = {}
    monkeypatch.setenv("OLLAMA_HOST", "https://untrusted.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.invalid")
    monkeypatch.setattr("model_gateway.ollama.Client", lambda **kwargs: captured.update(kwargs) or captured)
    local_client()
    assert captured["host"] == "http://127.0.0.1:11434"
    assert captured["trust_env"] is False and captured["follow_redirects"] is False


@pytest.mark.parametrize("ram,model,size", [(8, "qwen3:4b", 4500), (12, "qwen3:8b", 5500), (24, "qwen3:8b", 5500), (32, "qwen3.8:27b", 7500)])
def test_hardware_boundaries(ram, model, size):
    profile = hardware_profile(ram)
    assert profile.recommended[0] == model and profile.chunk_chars == size
    assert profile.output_tokens == 1024


def test_model_dict_and_object_responses():
    response = {"models": [{"name": "qwen3:4b", "size": 200}, {"model": "x-cloud", "size": 200}, {"model": "stub", "size": 0}]}
    assert list_installed_models(SimpleNamespace(list=lambda: response)) == ["qwen3:4b"]
    response = SimpleNamespace(models=[SimpleNamespace(model="qwen3:4b", size=200)])
    assert list_installed_models(SimpleNamespace(list=lambda: response)) == ["qwen3:4b"]


def test_model_errors_and_download_gate():
    def fail():
        raise ConnectionError()
    with pytest.raises(ModelError):
        list_installed_models(SimpleNamespace(list=fail))
    with pytest.raises(ModelError):
        download_model("qwen3:4b", lambda *_: None)
    with pytest.raises(ModelError):
        ensure_local_model(SimpleNamespace(), "model-cloud")
    fake = SimpleNamespace(list=lambda: {"models": [{"name": "alias", "size": 99}]}, show=lambda _: {"remote_host": "https://ollama.com"})
    with pytest.raises(ModelError, match="Remote"):
        ensure_local_model(fake, "alias")


class FakeModel:
    """Schema-compliant deterministic fixture, explicitly not AI inference."""
    def __init__(self, malformed=False):
        self.malformed = malformed
        self.prompts = []

    def list(self):
        return {"models": [{"model": "qwen3:4b", "size": 200}]}

    def show(self, model):
        return {"modelfile": "FROM local-weights"}

    def chat(self, **kwargs):
        assert kwargs["format"] == "json"
        assert kwargs["think"] is False
        assert kwargs["options"]["num_predict"] == 1024
        self.prompts.append(kwargs)
        if self.malformed:
            return {"message": {"content": "malformed response"}}
        if "STAGE 1:" in kwargs["messages"][0]["content"]:
            data = {"facts": [{"field": "Governing Law", "value": "Texas", "quote": "This Agreement is governed by the laws of the State of Texas, without regard to its conflict-of-laws principles.", "page": 2}], "glossary": [{"term": "Indemnifying Party", "definition": "Northstar Analytics LLC only", "quote": '"Indemnifying Party" means Northstar Analytics LLC, regardless of which Party disclosed or received the information at issue.', "page": 2}]}
        else:
            data = {"risks": [
                {"severity": "HIGH", "page": 99, "quote": "This Agreement is governed by the laws of the State of Texas, without regard to its conflict-of-laws principles.", "trap_explanation": "Texas conflicts with the DE/NY/MN playbook.", "suggested_redline": "This Agreement is governed by the laws of the State of Delaware."},
                {"severity": "HIGH", "page": 2, "quote": "The Indemnifying Party shall defend, indemnify and hold harmless the Protected Parties from third-party claims, damages and reasonable expenses arising from any unauthorized disclosure of Confidential Information by the Indemnifying Party.", "trap_explanation": "Section 6 defines only Northstar as the Indemnifying Party, making Section 7 unilateral.", "suggested_redline": "Each Party shall indemnify the other Party for third-party claims arising from its own unauthorized disclosure, subject to Section 8."},
                {"severity": "MEDIUM", "page": 1, "quote": "This Agreement automatically renews for successive one-year terms unless either Party gives written notice of non-renewal at least 15 days before the end of the then-current term.", "trap_explanation": "15 days falls below the 60-day playbook requirement.", "suggested_redline": "Replace 15 days with 60 days in Section 4."}]}
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)), done_reason="stop")


def test_two_page_sample_stays_one_segment_and_two_model_calls():
    source = parse_document((ROOT / "sample_test_nda.txt").read_bytes(), "sample_test_nda.txt")
    original = source.text
    chunks, _ = plan_audit_chunks(source, DEFAULT_PLAYBOOK, hardware_profile(8))
    assert len(chunks) == 1 and len(chunks[0].text) <= 4500
    assert chunks[0].pages == (1, 2)
    assert "[Page 1]" in chunks[0].text and "[Page 2]" in chunks[0].text
    assert source.text == original
    from document_engine import normalize
    assert normalize(chunks[0].text) == normalize(original)
    client = FakeModel()
    result = audit_document(source, "QA", "Firm", "qwen3:4b", client=client, profile=hardware_profile(8))
    assert result.complete and result.chunks == 1 and result.calls == 2
    assert len(client.prompts) == 2
    assert all("[Page 1]" in call["messages"][-1]["content"] and "[Page 2]" in call["messages"][-1]["content"] for call in client.prompts)


def test_legacy_sdk_think_fallback_preserves_request():
    calls = []
    class LegacyClient:
        def chat(self, *, model, messages, options, format, stream):
            calls.append({"model": model, "messages": messages, "options": options, "format": format, "stream": stream})
            return "legacy response"
    messages = [{"role": "user", "content": "Review this source."}]
    response = chat_without_thinking(LegacyClient(), model="qwen3:4b", messages=messages, options={"num_ctx": 4096}, format="json", stream=False, think=True)
    assert response == "legacy response" and len(calls) == 1
    assert calls[0]["options"] == {"num_ctx": 4096, "num_predict": 1024}
    assert calls[0]["messages"][-1]["content"].endswith("/no_think")
    assert messages[-1]["content"] == "Review this source."


def test_legacy_server_think_fallback():
    import ollama
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        if "think" in kwargs:
            raise ollama.ResponseError('unknown field "think"', status_code=400)
        return "ok"
    response = chat_without_thinking(SimpleNamespace(chat=chat), messages=[{"role": "user", "content": "Test"}])
    assert response == "ok" and len(calls) == 2
    assert calls[0]["think"] is False and "think" not in calls[1]


@pytest.mark.parametrize("error", [TypeError("unrelated type error"), ConnectionError("offline")])
def test_thinking_fallback_does_not_retry_unrelated_errors(error):
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        raise error
    with pytest.raises(type(error)):
        chat_without_thinking(SimpleNamespace(chat=chat), messages=[])
    assert len(calls) == 1


def test_unicode_and_long_sources_still_use_context_budget():
    source = SourceDocument("unicode.txt", "a" * 64, [Page(1, "契約 条項 " * 900)])
    profile = hardware_profile(8)
    chunks, _ = plan_audit_chunks(source, DEFAULT_PLAYBOOK, profile)
    assert len(chunks) > 1 and max(len(c.text) for c in chunks) <= 4500
    assert all(estimate_prompt_tokens(c.text) + profile.output_tokens < profile.context_tokens for c in chunks)


@pytest.fixture
def sample_result():
    source = parse_document((ROOT / "sample_test_nda.txt").read_bytes(), "sample_test_nda.txt")
    return audit_document(source, "QA Reviewer", "Example Firm", "qwen3:4b", client=FakeModel(), profile=hardware_profile(8))


def test_pipeline_detects_fixture_traps_and_corrects_page(sample_result):
    assert sample_result.complete
    assert sample_result.risk_counts == {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
    assert all(r.evidence.verification.verified for r in sample_result.risks)
    assert sample_result.risks[0].evidence.claimed_page == 99
    assert sample_result.risks[0].evidence.page == 2


def test_failed_model_never_looks_clean():
    source = parse_document(b"A contract without valid model responses.", "x.txt")
    result = audit_document(source, "QA", "Firm", "qwen3:4b", client=FakeModel(malformed=True), profile=hardware_profile(8))
    assert not result.complete and result.failures and not result.risks


def test_license_guard_stops_inference():
    client = FakeModel()
    source = parse_document(b"A contract", "x.txt")
    def guard():
        raise LicenseError("expired")
    with pytest.raises(LicenseError):
        audit_document(source, "QA", "Firm", "qwen3:4b", client=client, guard=guard)
    assert not client.prompts


def test_audit_log_metadata_only_and_idempotent(tmp_path, sample_result):
    path = tmp_path / "audit_log.json"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: append_audit_log(sample_result, path), range(8)))
    rows = json.loads(path.read_text())
    assert len(rows) == 1 and rows[0]["sha256"] == sample_result.sha256
    assert "quote" not in path.read_text() and "suggested_redline" not in path.read_text()
    path.write_text("broken json")
    with pytest.raises(ValueError, match="damaged"):
        append_audit_log(sample_result, path)
    assert path.read_text() == "broken json"


def test_incomplete_not_logged(tmp_path, sample_result):
    sample_result.failures.append("Model timed out")
    with pytest.raises(ValueError):
        append_audit_log(sample_result, tmp_path / "audit_log.json")


def test_word_export_metadata_tables_and_colors(sample_result):
    buffer = build_report(sample_result)
    assert isinstance(buffer, io.BytesIO) and buffer.tell() == 0
    doc = Document(buffer)
    xml = doc.element.xml
    assert sample_result.sha256 in xml and "Firm Audit Certificate" in xml
    assert "Defined Terms Glossary" in xml and "Suggested redline" in xml
    assert "FEE2E2" in xml and "FEF3C7" in xml and "tblHeader" in xml
    assert "Disclaimer:" in xml and len(doc.tables) == 4
    assert doc.sections[-1].page_width > doc.sections[-1].page_height


def test_streamlit_free_workspace_sample_and_clear(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("VERIPACT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("model_gateway.list_installed_models", lambda: ["qwen3:4b"])
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    assert not (tmp_path / ".vp_state.dat").exists()
    assert not any("Trial" in b.label or "Activate" in b.label for b in app.button)
    next(t for t in app.text_input if t.label == "Attorney / Reviewer Name").set_value("QA Reviewer").run()
    next(b for b in app.button if b.label == "Load sample NDA").click().run()
    assert not app.exception and "source" in app.session_state
    next(b for b in app.button if b.label == "Clear document & review").click().run()
    assert not app.exception and "source" not in app.session_state


@pytest.mark.parametrize("legacy_state", [None, "damaged legacy state", '{"clock_locked": true, "expires": "2000-01-01"}'])
def test_free_workspace_never_imports_licensing_or_touches_state(tmp_path, monkeypatch, legacy_state):
    import builtins
    from streamlit.testing.v1 import AppTest
    path = tmp_path / ".vp_state.dat"
    if legacy_state is not None:
        path.write_text(legacy_state, encoding="utf-8")
    monkeypatch.setenv("VERIPACT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("model_gateway.list_installed_models", lambda: ["qwen3:4b"])
    original_import = builtins.__import__
    def without_licensing(name, *args, **kwargs):
        if name in {"license_manager", "generate_license"}:
            raise AssertionError("The free app must not import legacy licensing")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_licensing)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    assert any(t.label == "Attorney / Reviewer Name" for t in app.text_input)
    assert any(b.label == "Load sample NDA" for b in app.button)
    assert not any("license key" in t.label.lower() for t in app.text_area)
    assert not (tmp_path / ".vp_state.dat.lock").exists()
    if legacy_state is None:
        assert not path.exists()
    else:
        assert path.read_text(encoding="utf-8") == legacy_state


@pytest.mark.parametrize("firm_name", ["", "Open Source Law LLP"])
def test_streamlit_completed_review(tmp_path, monkeypatch, sample_result, firm_name):
    from dataclasses import replace
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("VERIPACT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("model_gateway.list_installed_models", lambda: ["qwen3:4b"])
    def review(source, reviewer, firm, *args, **kwargs):
        assert "guard" not in kwargs
        return replace(sample_result, reviewer=reviewer, firm=firm)
    monkeypatch.setattr("audit_engine.audit_document", review)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    next(t for t in app.text_input if t.label == "Attorney / Reviewer Name").set_value("QA Reviewer").run()
    next(t for t in app.text_input if t.label == "Firm / Organization (optional)").set_value(firm_name).run()
    next(b for b in app.button if b.label == "Load sample NDA").click().run()
    next(b for b in app.button if b.label == "Run two-stage audit").click().run()
    assert not app.exception
    assert "report" in app.session_state and (tmp_path / "audit_log.json").exists()
    assert len(app.tabs) == 4
    expected_firm = firm_name or "Independent review"
    assert app.session_state.result.firm == expected_firm
    assert expected_firm in Document(io.BytesIO(app.session_state.report)).element.xml
    assert not (tmp_path / ".vp_state.dat").exists()


if __name__ == "__main__":
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    raise SystemExit(pytest.main([str(Path(__file__).resolve()), "-q", "-W", "error", *sys.argv[1:]]))
