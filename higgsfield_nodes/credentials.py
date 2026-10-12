"""Higgsfield API key resolution and local storage.

Resolution order:
  1. HF_KEY                              "KEY_ID:KEY_SECRET" (Higgsfield's documented variable)
  2. HF_API_KEY_ID + HF_API_KEY_SECRET   (split form used in Higgsfield's curl examples)
  3. the key saved from ComfyUI Settings  (<ComfyUI user dir>/higgsfield/credentials.json)

The saved key is encrypted with Windows DPAPI (bound to the current Windows user) when
available, otherwise stored with owner-only permissions. It never enters node inputs,
workflow JSON, or any HTTP response.
"""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


class CredentialError(Exception):
    pass


@dataclass(frozen=True, repr=False)
class Credentials:
    key_id: str
    secret: str
    source: str

    @property
    def header_value(self) -> str:
        return f"Key {self.key_id}:{self.secret}"

    @property
    def masked(self) -> str:
        return mask(self.key_id)

    def __repr__(self) -> str:  # never print the secret
        return f"Credentials(key_id={self.masked!r}, source={self.source!r})"


def mask(key_id: str) -> str:
    if len(key_id) <= 6:
        return "•" * len(key_id)
    return f"{key_id[:4]}…{key_id[-2:]}"


def parse_combined(value: str) -> tuple[str, str]:
    key_id, sep, secret = value.strip().partition(":")
    if not sep or not key_id.strip() or not secret.strip():
        raise CredentialError("The key must be in KEY_ID:KEY_SECRET format (as shown in the Higgsfield console).")
    if any(c.isspace() for c in key_id.strip() + secret.strip()):
        raise CredentialError("The key must not contain spaces or line breaks.")
    return key_id.strip(), secret.strip()


# --------------------------------------------------------------------------- storage

def user_directory() -> Path:
    try:
        import folder_paths

        return Path(folder_paths.get_user_directory())
    except Exception:
        return Path.cwd() / "user"


def store_path() -> Path:
    return user_directory() / "higgsfield" / "credentials.json"


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    # flags: CRYPTPROTECT_UI_FORBIDDEN (0x1)
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
    if not ok:
        raise CredentialError("Windows could not " + ("encrypt" if encrypt else "decrypt") + " the saved key.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def save(key_id: str, secret: str, path: Path | None = None) -> None:
    path = path or store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    plain = json.dumps({"key_id": key_id, "secret": secret}).encode("utf-8")
    if sys.platform == "win32":
        record = {"version": 1, "protection": "dpapi", "data": base64.b64encode(_dpapi(plain, True)).decode("ascii")}
    else:
        record = {"version": 1, "protection": "none", "data": base64.b64encode(plain).decode("ascii")}
    fd, temporary = tempfile.mkstemp(prefix=".credentials.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        if os.name != "nt":
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def load(path: Path | None = None) -> tuple[str, str] | None:
    path = path or store_path()
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        raw = base64.b64decode(record["data"])
        if record.get("protection") == "dpapi":
            raw = _dpapi(raw, False)
        data = json.loads(raw.decode("utf-8"))
        return str(data["key_id"]), str(data["secret"])
    except CredentialError:
        raise
    except Exception as error:
        raise CredentialError("The saved Higgsfield key is unreadable; set it again in ComfyUI Settings.") from error


def delete(path: Path | None = None) -> None:
    (path or store_path()).unlink(missing_ok=True)


# --------------------------------------------------------------------------- resolution

def resolve(environ: dict[str, str] | None = None, path: Path | None = None) -> Credentials | None:
    env = os.environ if environ is None else environ
    if env.get("HF_KEY"):
        key_id, secret = parse_combined(env["HF_KEY"])
        return Credentials(key_id, secret, "environment (HF_KEY)")
    key_id, secret = env.get("HF_API_KEY_ID"), env.get("HF_API_KEY_SECRET")
    if key_id and secret:
        return Credentials(key_id.strip(), secret.strip(), "environment (HF_API_KEY_ID/HF_API_KEY_SECRET)")
    stored = load(path)
    if stored:
        return Credentials(stored[0], stored[1], "ComfyUI settings")
    return None


def require(environ: dict[str, str] | None = None, path: Path | None = None) -> Credentials:
    credentials = resolve(environ, path)
    if credentials is None:
        raise CredentialError(
            "No Higgsfield API key is set. Open ComfyUI Settings → Higgsfield → API key, "
            "or set the HF_KEY environment variable to KEY_ID:KEY_SECRET."
        )
    return credentials


def managed_by_environment(environ: dict[str, str] | None = None) -> bool:
    """True when the key comes from the server environment (cloud/shared setups): the UI must not change it."""
    env = os.environ if environ is None else environ
    return bool(env.get("HF_KEY") or (env.get("HF_API_KEY_ID") and env.get("HF_API_KEY_SECRET")))


def storage_description() -> str:
    if sys.platform == "win32":
        return "Stored only on this computer, encrypted with your Windows login."
    return "Stored in ComfyUI's user folder on this server, readable only by its user account."


def status(environ: dict[str, str] | None = None, path: Path | None = None) -> dict:
    """Non-secret summary for the settings panel."""
    base = {"managed_by_env": managed_by_environment(environ), "storage": storage_description()}
    try:
        credentials = resolve(environ, path)
    except CredentialError as error:
        return base | {"configured": False, "source": None, "key_id": None, "error": str(error)}
    if credentials is None:
        return base | {"configured": False, "source": None, "key_id": None}
    return base | {"configured": True, "source": credentials.source, "key_id": credentials.masked}
