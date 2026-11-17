"""Legacy standalone offline licensing utility; unused by the MIT application.

The free auditor neither imports this module nor reads its state. Retained for
historical reference and independent utility tests. Signing keys stay offline.
Local state is tamper evident, not hardware backed; an administrator who edits
code or restores/deletes all state can bypass it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from filelock import FileLock

from storage import atomic_json

# Replace with YOUR public key using generate_license.py keygen --install-public-key.
# No corresponding private signing key is distributed with this application.
PUBLIC_KEY_B64 = "7wDSsWmLsCpYgmoVe2Kt620z2AjWk14/Qi+GmgdikfA="
STATE_SALT = b"VeriPact/local-state/v1/2026/clock-high-water"
UTC = timezone.utc


class LicenseError(ValueError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def decode(data: str) -> bytes:
    return base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)


def utc_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        raise LicenseError("An aware UTC timestamp is required.")
    return value.astimezone(UTC)


def verify_license(key: str, public_key: bytes | None = None, now: datetime | None = None) -> dict:
    try:
        if len(key) > 8192:
            raise ValueError("oversize")
        prefix, payload, signature = key.strip().split(".")
        if prefix != "VP1":
            raise ValueError("version")
        raw = decode(payload)
        verifier = Ed25519PublicKey.from_public_bytes(public_key or base64.b64decode(PUBLIC_KEY_B64))
        verifier.verify(decode(signature), raw)
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"version", "firm", "expires"}:
            raise ValueError("schema")
        if data["version"] != 1 or not isinstance(data["firm"], str) or not 1 <= len(data["firm"].strip()) <= 200:
            raise ValueError("firm")
        expiry = date.fromisoformat(data["expires"])
        if expiry.isoformat() != data["expires"]:
            raise ValueError("date format")
    except (ValueError, TypeError, KeyError, InvalidSignature, UnicodeError) as exc:
        raise LicenseError("Invalid license signature or format.") from exc
    # A date-based license remains valid for the entire indicated UTC day.
    if utc_now(now).date() > expiry:
        raise LicenseError(f"License expired on {expiry.isoformat()} UTC.")
    return data


@dataclass(frozen=True)
class LicenseStatus:
    active: bool
    mode: str
    firm: str = ""
    expires: str = ""
    message: str = ""


class LicenseManager:
    def __init__(self, state_path: Path | str | None = None, public_key: bytes | None = None):
        root = Path(os.environ.get("VERIPACT_DATA_DIR", Path(__file__).resolve().parent))
        self.path = Path(state_path) if state_path else root / ".vp_state.dat"
        self.public_key = public_key

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "last_seen": None, "trial_started": None, "license": None, "clock_locked": False}
        try:
            packet = json.loads(self.path.read_text(encoding="utf-8"))
            state = packet["state"]
            expected = hmac.new(STATE_SALT, canonical(state), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(packet["mac"], expected):
                raise ValueError("mac")
            if set(state) != {"version", "last_seen", "trial_started", "license", "clock_locked"} or state["version"] != 1:
                raise ValueError("schema")
            for field in ("last_seen", "trial_started"):
                if state[field] is not None:
                    utc_now(datetime.fromisoformat(state[field]))
            if not isinstance(state["clock_locked"], bool):
                raise ValueError("lock")
            if state["license"] is not None and not isinstance(state["license"], str):
                raise ValueError("license")
            return state
        except (OSError, ValueError, TypeError, KeyError, LicenseError) as exc:
            raise LicenseError("Local license state is damaged or modified. Contact the license administrator.") from exc

    def _write(self, state: dict) -> None:
        atomic_json(self.path, {"state": state, "mac": hmac.new(STATE_SALT, canonical(state), hashlib.sha256).hexdigest()})

    def _tick(self, state: dict, now: datetime) -> None:
        if state["clock_locked"]:
            raise LicenseError("Clock rollback detected. This installation is locked; contact the license administrator.")
        last = state["last_seen"]
        if last and now < utc_now(datetime.fromisoformat(last)):
            state["clock_locked"] = True
            self._write(state)
            raise LicenseError("Clock rollback detected. This installation is locked; contact the license administrator.")
        state["last_seen"] = now.isoformat()
        self._write(state)

    def _lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self.path) + ".lock", timeout=10)

    def status(self, now: datetime | None = None) -> LicenseStatus:
        try:
            with self._lock():
                moment = utc_now(now)  # Read clock inside lock to avoid concurrent false rollbacks.
                state = self._read()
                self._tick(state, moment)
                if state["license"]:
                    data = verify_license(state["license"], self.public_key, moment)
                    return LicenseStatus(True, "Licensed", data["firm"], data["expires"])
                if state["trial_started"]:
                    end = utc_now(datetime.fromisoformat(state["trial_started"])) + timedelta(days=7)
                    if moment < end:
                        return LicenseStatus(True, "Trial", "Trial workspace", end.isoformat())
                    return LicenseStatus(False, "Expired", message="Your seven-day trial has ended. Activate a firm license.")
                return LicenseStatus(False, "Inactive", message="Activate your firm license or begin a seven-day trial.")
        except (LicenseError, OSError, TimeoutError) as exc:
            return LicenseStatus(False, "Locked", message=str(exc))

    def start_trial(self, now: datetime | None = None) -> None:
        with self._lock():
            moment = utc_now(now)
            state = self._read()
            self._tick(state, moment)
            if state["trial_started"] is not None:
                raise LicenseError("A trial has already been started on this installation.")
            if state["license"]:
                raise LicenseError("This installation already has a license.")
            state["trial_started"] = moment.isoformat()
            self._write(state)

    def activate(self, key: str, now: datetime | None = None) -> None:
        with self._lock():
            moment = utc_now(now)
            state = self._read()
            self._tick(state, moment)
            verify_license(key, self.public_key, moment)
            state["license"] = key.strip()
            self._write(state)
