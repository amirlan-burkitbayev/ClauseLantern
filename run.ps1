$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:OLLAMA_NO_CLOUD = '1'
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = 'false'
& "$PSScriptRoot\.venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.headless true
