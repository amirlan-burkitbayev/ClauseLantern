# ClauseLantern sample NDA walkthrough

Load the included fictional NDA, enter a reviewer name, and keep the example playbook. Select **Run two-stage audit**. The model first extracts key terms, then checks the agreement against the playbook. Open an individual finding, compare its quote with the original wording, and download the Word report.

| Compare against the sample | Expected issue |
| --- | --- |
| Governing law | Texas is outside the example playbook. |
| Renewal notice | The sample gives 15 days, below the example 60-day minimum. |
| Indemnification | Defined roles make an apparently reciprocal obligation unilateral. |

These are pilot expectations, not a recorded model result. Record any missed or incorrect findings and whether you would use the report for a first pass. For benchmark runs, record the model, OS, available RAM, time from the click to the report, and whether this was the first run after starting Ollama.

## First-run troubleshooting

- No models listed: start the local Ollama service and run `ollama list`. Pull `qwen3:4b` if the list is empty.
- Python imports fail: use the same virtual environment interpreter for installation and Streamlit startup. See [Quickstart](README.md#quickstart--3-steps).
- Slow or incomplete audit: check free memory and Ollama logs. Include the elapsed time and error in the pilot report. A sub-30-second review has not been verified.
- Port 8501 is occupied: start Streamlit with `--server.port 8502` and use `http://127.0.0.1:8502`.
