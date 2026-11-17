"""Keep Streamlit's Starlette multipart transport from spooling files to disk."""
from __future__ import annotations


def configure_memory_only_uploads() -> None:
    from starlette.formparsers import MultiPartParser

    if not hasattr(MultiPartParser, "spool_max_size"):
        raise RuntimeError("Unsupported upload transport. Install the validated requirements before loading documents.")
    # Python SpooledTemporaryFile(max_size=0) disables automatic disk rollover.
    # Streamlit separately caps incoming request bytes using maxUploadSize.
    # Set this before the session can create any file_uploader widget. This is
    # server-process-wide, so concurrent sessions inherit the same protection.
    MultiPartParser.spool_max_size = 0
