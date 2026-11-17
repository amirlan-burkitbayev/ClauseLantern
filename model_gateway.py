"""Ollama is reachable only over loopback; reject cloud-backed model manifests."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import ollama
import psutil

# The Ollama daemon must ALSO be launched with OLLAMA_NO_CLOUD=1.
os.environ["OLLAMA_NO_CLOUD"] = "1"
LOCAL_HOST = "http://127.0.0.1:11434"


class ModelError(RuntimeError):
    pass


def field(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


@dataclass(frozen=True)
class HardwareProfile:
    ram_gb: float
    recommended: tuple[str, ...]
    chunk_chars: int
    context_tokens: int
    mode: str

    @property
    def output_tokens(self) -> int:
        return 1024


def hardware_profile(ram_gb: float | None = None) -> HardwareProfile:
    ram = ram_gb if ram_gb is not None else psutil.virtual_memory().total / 1024 ** 3
    if ram < 12:
        return HardwareProfile(ram, ("qwen3:4b",), 4500, 4096, "Safe Mode")
    if ram <= 24:
        return HardwareProfile(ram, ("qwen3:8b", "qwen3:14b"), 5500, 12288, "Balanced")
    return HardwareProfile(ram, ("qwen3.8:27b", "gemma4:31b"), 7500, 16384, "Expanded")


def local_client(timeout: float = 900.0) -> ollama.Client:
    # Ignore HTTP(S)_PROXY and OLLAMA_HOST: contract bytes must never use a proxy.
    return ollama.Client(host=LOCAL_HOST, timeout=timeout, trust_env=False, follow_redirects=False)


def chat_without_thinking(client, **kwargs):
    """Disable Qwen thinking, including a narrow compatibility path for old SDKs.

    The prompt instruction is a best-effort fallback when the SDK or daemon
    rejects the think parameter. Other inference/type errors are never retried.
    """
    kwargs.pop("think", None)
    kwargs["options"] = {**kwargs.get("options", {}), "num_predict": 1024}
    messages = [dict(message) for message in kwargs.get("messages", [])]
    for message in reversed(messages):
        if message.get("role") == "user":
            message["content"] = str(message.get("content", "")).rstrip() + "\n/no_think"
            break
    kwargs["messages"] = messages
    try:
        return client.chat(**{**kwargs, "think": False})
    except TypeError as exc:
        if not re.search(r"unexpected keyword argument ['\"]think['\"]", str(exc)):
            raise
    except ollama.ResponseError as exc:
        # Match only a rejected request field, not network/model/runtime errors.
        if exc.status_code != 400 or not re.search(
            r"(?:unknown|unrecognized|unsupported) (?:field|parameter|argument) [\"']?think\b",
            str(exc), re.IGNORECASE,
        ):
            raise
    return client.chat(**kwargs)


def list_installed_models(client=None) -> list[str]:
    try:
        response = (client or local_client(5)).list()
        names = []
        for model in field(response, "models", []) or []:
            name = field(model, "model") or field(model, "name")
            size = field(model, "size", 0) or 0
            if isinstance(name, str) and "cloud" not in name.lower() and size > 0:
                names.append(name)
        return sorted(set(names))
    except Exception as exc:
        raise ModelError("Local Ollama is unavailable. Start Ollama on 127.0.0.1:11434 and refresh models.") from exc


def ensure_local_model(client, model: str) -> None:
    if not model or "cloud" in model.lower() or model not in list_installed_models(client):
        raise ModelError("Choose an installed local model. Cloud models are blocked.")
    try:
        info = client.show(model)
        # Check remote aliases too, not just names ending in -cloud.
        if field(info, "remote_model") or field(info, "remote_host"):
            raise ModelError("Remote-backed models are blocked.")
        manifest = str(field(info, "modelfile", "")).lower()
        if "remote_host" in manifest or "remote_model" in manifest or "ollama.com" in manifest:
            raise ModelError("Remote-backed models are blocked.")
    except ModelError:
        raise
    except Exception as exc:
        raise ModelError("Cannot verify that the selected model is local.") from exc


def download_model(model: str, progress, connected_setup: bool = False, client=None) -> None:
    if not connected_setup:
        raise ModelError("Model downloads require explicit connected setup mode.")
    allowed = {"qwen3:4b", "qwen3:8b", "qwen3:14b", "qwen3.8:27b", "gemma4:31b"}
    if model not in allowed:
        raise ModelError("Only the recommended local models may be downloaded here.")
    try:
        for item in (client or local_client(600)).pull(model, stream=True):
            total, completed = field(item, "total", 0) or 0, field(item, "completed", 0) or 0
            progress(min(completed / total, 1.0) if total else 0.0, str(field(item, "status", "Downloading")))
    except Exception as exc:
        raise ModelError("Model download failed. Check setup connectivity and disk space, then retry.") from exc
