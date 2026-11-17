"""VeriPact AI — free, MIT-licensed localhost attorney review workspace."""
from __future__ import annotations

import os
from pathlib import Path

os.environ["OLLAMA_NO_CLOUD"] = "1"
import streamlit as st
import psutil

from audit_engine import DEFAULT_PLAYBOOK, DISCLAIMER, FACT_FIELDS, append_audit_log, audit_document, plan_audit_chunks
from document_engine import DocumentError, parse_document
from model_gateway import ModelError, download_model, hardware_profile, list_installed_models
from report_builder import build_report
from privacy import configure_memory_only_uploads

configure_memory_only_uploads()

ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("VERIPACT_DATA_DIR", ROOT))
CSS = """
<style>
#MainMenu,footer,[data-testid="stToolbar"]{display:none}
.stApp{background:#F5F7FA;color:#0F172A}
.block-container{max-width:1250px;padding-top:1.8rem;padding-bottom:3rem}
[data-testid="stSidebar"]{background:#0F172A}
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] p,[data-testid="stSidebar"] label,[data-testid="stSidebar"] .stCaption{color:#E2E8F0}
h1,h2,h3{letter-spacing:-.035em} h1{font-weight:750!important}
.security{background:#0F172A;color:#FFF;padding:13px 20px;border-radius:8px;font-size:12px;letter-spacing:.02em;margin-bottom:30px;border-left:4px solid #D97706}
.eyebrow{font-size:11px;letter-spacing:.2em;color:#9A5800;font-weight:750;text-transform:uppercase}
.hero{display:flex;justify-content:space-between;align-items:center;gap:20px;margin:12px 0 25px}
.hero h1{font-size:40px;margin:0;color:#0F172A}.hero p{color:#64748B;margin:6px 0 0}
.wordmark{font-size:22px;font-weight:700;letter-spacing:-.05em;margin-bottom:3px}
.gold{color:#FBBF24}.badge{border:1px solid #CBD5E1;border-radius:50px;padding:8px 13px;font-size:11px;white-space:nowrap;letter-spacing:.08em}
[data-testid="stMetric"]{background:white;border:1px solid #E2E8F0;border-radius:10px;padding:15px 20px}
[data-testid="stMetricValue"]{color:#0F172A}
.disclaimer{border-top:1px solid #CBD5E1;margin-top:36px;padding-top:18px;color:#64748B;font-size:11px;line-height:1.7}
.stButton>button[kind="primary"]{background:#D97706;border-color:#D97706;color:white;font-weight:650}
[data-testid="stFileUploader"]{background:#FFF;padding:12px;border-radius:12px;border:1px solid #E2E8F0}
@media(max-width:700px){.hero{display:block}.hero h1{font-size:30px}.badge{display:inline-block;margin-top:15px}}
</style>
"""


def disclaimer():
    st.markdown(f'<div class="disclaimer">{DISCLAIMER}</div>', unsafe_allow_html=True)


def plain_label(value: str) -> str:
    """Prevent document/model strings from injecting Markdown image requests."""
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("<", "&lt;")


def clear_review():
    for key in ("result", "report", "source", "log_error", "use_sample", "run_identity"):
        st.session_state.pop(key, None)
    st.session_state["upload_version"] = st.session_state.get("upload_version", 0) + 1


def render_evidence(evidence):
    if evidence.verification.verified:
        st.success(evidence.verification.label)
    else:
        st.warning(evidence.verification.label + f" · Model claimed page {evidence.claimed_page}")
    st.text(evidence.quote)
    if evidence.page and evidence.page != evidence.claimed_page:
        st.caption(f"Citation corrected from model page {evidence.claimed_page} to source page {evidence.page}.")


def render_result(result):
    st.divider()
    st.subheader("Review findings")
    st.caption(plain_label(f"{result.file_name} · {result.reviewer} · {result.model} · {result.timestamp}"))
    if result.complete:
        st.success("Review processing completed. Attorney verification is required.")
    else:
        st.error("Incomplete review. Failed segments must be reviewed again before relying on this report.")
        for issue in result.failures:
            st.warning(issue)
    if st.session_state.get("log_error"):
        st.error(st.session_state.log_error)
        if st.button("Retry saving audit metadata"):
            try:
                append_audit_log(result, DATA_ROOT / "audit_log.json")
                st.session_state.pop("log_error", None)
                st.rerun()
            except (OSError, ValueError, TimeoutError) as exc:
                st.error(str(exc))
    counts = result.risk_counts
    for col, label in zip(st.columns(4), ["HIGH", "MEDIUM", "LOW", "SOURCE CHECKED"]):
        value = counts[label] if label in counts else f"{sum(r.evidence.verification.verified for r in result.risks)}/{len(result.risks)}"
        col.metric(label, value)
    risk_tab, fact_tab, terms_tab, record_tab = st.tabs(["Risks & redlines", "Fact sheet", "Defined terms", "Review record"])
    with risk_tab:
        if not result.risks:
            st.info("The model returned no deviations. This is not an assurance of compliance; independently review the agreement.")
        for index, risk in enumerate(result.risks, 1):
            with st.expander(f"{risk.severity} · Finding {index} · Page {risk.evidence.page or 'unverified'}", expanded=True):
                render_evidence(risk.evidence)
                st.markdown("**Trap explanation**")
                st.text(risk.trap_explanation)
                st.markdown("**Suggested redline · attorney approval required**")
                st.text(risk.suggested_redline)
    with fact_tab:
        for name in FACT_FIELDS:
            st.markdown(f"**{name}**")
            items = [f for f in result.facts if f.field == name]
            if not items:
                st.caption("Not found — verify manually")
            for fact in items:
                st.text(fact.value)
                with st.expander("Supporting source", expanded=False):
                    render_evidence(fact.evidence)
    with terms_tab:
        if not result.glossary:
            st.info("No defined terms were extracted. Verify the source manually.")
        for term in result.glossary:
            with st.expander(plain_label(term.term)):
                st.text(term.definition)
                render_evidence(term.evidence)
    with record_tab:
        st.text(f"Firm: {result.firm}\nReviewer: {result.reviewer}\nReview ID: {result.review_id}\nPagination: {result.pagination}\nSegments: {result.chunks} · Model calls: {result.calls}")
        st.code(result.sha256, language=None)
        st.caption("SHA-256 fingerprint of the original uploaded bytes")
        st.text(result.playbook)
        for warning in result.warnings:
            st.warning(warning)
        st.caption("Source matching checks wording, not legal accuracy. Fuzzy matches require comparison with the original.")
    if "report" not in st.session_state:
        st.session_state.report = build_report(result).getvalue()
    prefix = "INCOMPLETE_" if not result.complete else ""
    st.download_button("Download executive Word report", data=st.session_state.report, file_name=prefix + Path(result.file_name).stem + "_VeriPact_Review.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", type="primary")


def main():
    st.set_page_config(page_title="VeriPact AI | Contract Auditor", page_icon="⚖️", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    banner = "CONNECTED MODEL SETUP · Contract review is disabled" if st.session_state.get("setup_mode") else "🔒 100% AIR-GAPPED &amp; ZERO-RETENTION: Local Execution Only. Client Data Never Leaves This Hardware."
    st.markdown(f'<div class="security">{banner}</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero"><div><div class="eyebrow">THE PRIVATE CONTRACT REVIEW WORKSPACE</div><h1>VeriPact AI<span style="color:#D97706">.</span></h1><p>Air-Gapped Contract Auditor</p></div><span class="badge">LOCAL EXECUTION · ATTORNEY LED</span></div>', unsafe_allow_html=True)
    st.caption("Document content stays in session memory. Audit metadata is retained locally; downloaded reports are saved by you. A true air gap requires network isolation of this hardware.")

    profile = hardware_profile()
    with st.sidebar:
        st.markdown('<div class="wordmark">VeriPact<span class="gold"> AI</span></div>', unsafe_allow_html=True)
        st.caption("COUNSEL'S WORKSPACE")
        st.divider()
        st.caption("FREE & OPEN SOURCE · MIT LICENSE")
        firm_name = st.text_input("Firm / Organization (optional)", max_chars=200, placeholder="e.g. Example Law LLP")
        firm = firm_name.strip() or "Independent review"
        reviewer = st.text_input("Attorney / Reviewer Name", max_chars=200, placeholder="e.g. Alex Morgan")
        playbook = st.text_area("Firm Playbook", value=DEFAULT_PLAYBOOK, height=260, max_chars=1600)
        st.divider()
        st.markdown("### Local model")
        st.caption(f"{profile.ram_gb:.1f} GB RAM · {profile.mode}\nDocument segments ≤ {profile.chunk_chars:,} characters")
        st.caption("Thinking off · Responses capped at 1,024 tokens")
        available_gb = psutil.virtual_memory().available / 1024 ** 3
        if available_gb < 3:
            st.warning(f"Only {available_gb:.1f} GB RAM is available. Close unused applications before loading a model; total RAM alone does not guarantee a safe fit.")
        if st.button("Refresh installed models"):
            st.session_state.pop("models", None)
        if "models" not in st.session_state:
            try:
                st.session_state.models = list_installed_models()
                st.session_state.pop("model_error", None)
            except ModelError as exc:
                st.session_state.model_error = str(exc)
        models = st.session_state.get("models", [])
        model = st.selectbox("Installed Ollama model", models, placeholder="No local model available") if models else None
        if st.session_state.get("model_error"):
            st.warning(st.session_state.model_error)
        st.caption("Recommended: " + " or ".join(profile.recommended))
        st.caption("RAM guidance is approximate; quantization, free memory and GPU capacity also affect fit.")
        with st.expander("Connected model setup"):
            st.caption("Setup requires internet access. Finish downloading before disconnecting this computer. Contract review is disabled while setup mode is enabled.")
            setup = st.checkbox("Enable connected setup", value=False, key="setup_mode")
            recommended = st.selectbox("Recommended model", profile.recommended)
            if st.button("Download Recommended Model", disabled=not setup):
                bar = st.progress(0.0, text="Connecting to local Ollama…")
                try:
                    download_model(recommended, lambda fraction, text: bar.progress(fraction, text=text), connected_setup=setup)
                    bar.progress(1.0, text="Model installed")
                    st.session_state.pop("models", None)
                    st.rerun()
                except ModelError as exc:
                    st.error(str(exc))
        if st.button("Clear document & review", width="stretch"):
            clear_review()
            st.rerun()

    if setup:
        st.warning("Connected setup is enabled. Turn it off before loading or reviewing client documents.")
        disclaimer()
        return
    st.subheader("Start a contract review")
    st.write("Add an agreement, confirm your playbook, and review every finding against its source.")
    left, right = st.columns([2, 1], gap="large")
    with left:
        uploaded = st.file_uploader("Contract document", type=["pdf", "docx", "txt"], key=f"upload_{st.session_state.get('upload_version', 0)}", help="Up to 25 MB. Scanned PDFs require local OCR. TXT must be UTF-8.")
    with right:
        with st.container(border=True):
            st.markdown("**Try the review workflow**")
            st.caption("A fictional two-page NDA contains three planted deviations from the default playbook.")
            if st.button("Load sample NDA"):
                clear_review()
                st.session_state.use_sample = True
                st.rerun()
    raw = uploaded.getvalue() if uploaded else None
    name = uploaded.name if uploaded else ""
    if not uploaded and st.session_state.get("use_sample"):
        raw = (ROOT / "sample_test_nda.txt").read_bytes()
        name = "sample_test_nda.txt"
    source = None
    if raw is not None:
        try:
            source = parse_document(raw, name)
            identity = (source.sha256, source.name)
            if st.session_state.get("run_identity") != identity:
                for key in ("result", "report", "log_error"):
                    st.session_state.pop(key, None)
            st.session_state.source = source
            st.session_state.run_identity = identity
            st.success(f"Ready · {source.name} · {len(source.pages)} page sections · {len(raw):,} bytes")
            st.caption(source.pagination)
            if playbook.strip():
                try:
                    planned, _ = plan_audit_chunks(source, playbook, profile)
                    if len(planned) == 1:
                        st.caption("1 document segment · one fact-extraction pass and one risk-review pass")
                    else:
                        st.caption(f"{len(planned)} document segments · two-stage review")
                except ValueError as exc:
                    st.warning(str(exc))
            for warning in source.warnings:
                st.warning(warning)
            with st.expander("Inspect extracted source"):
                st.text(source.text)
        except DocumentError as exc:
            for key in ("result", "report", "source"):
                st.session_state.pop(key, None)
            st.error(str(exc))
    else:
        for key in ("result", "report", "source"):
            st.session_state.pop(key, None)
    if st.button("Run two-stage audit", type="primary", disabled=not (source and model and reviewer.strip() and playbook.strip()), width="stretch"):
        progress = st.progress(0.0, text="Preparing local review…")
        for key in ("result", "report", "log_error"):
            st.session_state.pop(key, None)
        try:
            result = audit_document(source, reviewer, firm, model, playbook, profile=profile, progress=lambda fraction, text: progress.progress(fraction, text=text))
            st.session_state.result = result
            if result.complete:
                try:
                    append_audit_log(result, DATA_ROOT / "audit_log.json")
                except (OSError, ValueError, TimeoutError) as exc:
                    st.session_state.log_error = f"Review completed, but audit metadata was not saved: {exc}"
        except (ValueError, ModelError, OSError, TimeoutError) as exc:
            st.error(str(exc))
    if "result" in st.session_state:
        try:
            previous = st.session_state.result
            if (previous.firm, previous.reviewer, previous.playbook, previous.model) != (firm, reviewer.strip(), playbook, model):
                st.info("These findings use the review settings shown below. Run another audit to apply your current sidebar settings.")
            render_result(st.session_state.result)
        except (ValueError, OSError) as exc:
            st.error(f"The Word report could not be prepared: {exc}")
    disclaimer()


if __name__ == "__main__":
    main()
