"""Explicit optional real-Ollama end-to-end check, using the synthetic NDA only."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from audit_engine import append_audit_log, audit_document
from document_engine import parse_document
from model_gateway import field, hardware_profile, local_client
from report_builder import build_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--output-dir", type=Path, default=Path(".qa"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source = parse_document(Path(__file__).with_name("sample_test_nda.txt").read_bytes(), "sample_test_nda.txt")
    class SyntheticTraceClient:
        # Tracing is confined to this explicitly synthetic QA utility. The app
        # never records prompts or response bodies.
        def __init__(self):
            self.client = local_client()
            self.count = 0
        def list(self):
            return self.client.list()
        def show(self, model):
            return self.client.show(model)
        def chat(self, **kwargs):
            self.count += 1
            response = self.client.chat(**kwargs)
            text = field(field(response, "message", {}), "content", "")
            (args.output_dir / f"synthetic_response_{self.count}.txt").write_text(text, encoding="utf-8")
            return response
    result = audit_document(source, "Live QA Reviewer", "VeriPact Synthetic QA", args.model, client=SyntheticTraceClient(), profile=hardware_profile(8), progress=lambda _, message: print(message, flush=True))
    (args.output_dir / "live_result.json").write_text(json.dumps(asdict(result), indent=2, ensure_ascii=False), encoding="utf-8")
    (args.output_dir / "sample_audit_report.docx").write_bytes(build_report(result).getvalue())
    if result.complete:
        append_audit_log(result, args.output_dir / "audit_log.json")
    explanations = " ".join(r.trap_explanation + " " + r.evidence.quote for r in result.risks).lower()
    checks = {"complete": result.complete, "governing_law_trap": "texas" in explanations, "renewal_trap": "15" in explanations and ("renew" in explanations or "notice" in explanations), "indemnification_trap": "indemnif" in explanations, "all_risk_quotes_verified": bool(result.risks) and all(r.evidence.verification.verified for r in result.risks)}
    (args.output_dir / "live_checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(json.dumps({"checks": checks, "risk_counts": result.risk_counts, "model_calls": result.calls}, indent=2), flush=True)
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
