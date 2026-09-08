from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

import sys


CREDENTIAL_PREFIX = "SlopeForge/PostgreSQL/"


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore(Protocol):
    def read(self, profile_id: str) -> str | None: ...
    def write(self, profile_id: str, username: str, password: str) -> None: ...
    def delete(self, profile_id: str) -> None: ...


def credential_target(profile_id: str) -> str:
    return f"{CREDENTIAL_PREFIX}{str(profile_id).strip()}"


def _windows_error_code(exc: Exception) -> str:
    """Expose only a safe Windows error code, never credential contents."""
    code = getattr(exc, "winerror", None)
    if code is None:
        args = getattr(exc, "args", ())
        if args and isinstance(args[0], int):
            code = args[0]
    return f"Windows error {code}" if code is not None else "Windows error unavailable"


def _credential_error_message(
    operation: str,
    target: str,
    exc: Exception,
    *,
    win32cred_imported: bool,
) -> str:
    """Return support-safe diagnostics without exposing credential contents."""
    error_code = _windows_error_code(exc)
    frozen = "yes" if getattr(sys, "frozen", False) else "no"
    imported = "yes" if win32cred_imported else "no"
    missing_module = getattr(exc, "name", None)
    module_detail = (
        f"; missing module: {missing_module}"
        if isinstance(missing_module, str) and missing_module.replace("_", "").replace(".", "").isalnum()
        else ""
    )
    return (
        f"Could not {operation} the saved database credential."
        f" (exception: {exc.__class__.__name__}; {error_code}"
        f"{module_detail}"
        f"; credential target: {target}; credential read: no"
        f"; frozen: {frozen}; sys.executable: {sys.executable}"
        f"; win32cred import: {imported})"
    )


class WindowsCredentialStore:
    """Store PostgreSQL passwords in Windows Credential Manager."""

    @staticmethod
    def _module(target: str):
        if sys.platform != "win32":
            raise CredentialStoreError("Windows Credential Manager is unavailable on this platform.")
        try:
            # win32cred lazily imports pywintypes while converting a present
            # Windows credential. Preload it before CredRead so the frozen
            # module loader and pywin32 runtime hooks are initialized first.
            import pythoncom  # noqa: F401
            import pywintypes  # noqa: F401
            import win32cred
            import win32timezone  # noqa: F401
        except ImportError as exc:  # pragma: no cover - Windows packaging guard
            raise CredentialStoreError(
                _credential_error_message(
                    "load", target, exc, win32cred_imported=False
                )
            ) from exc
        return win32cred

    def read(self, profile_id: str) -> str | None:
        target = credential_target(profile_id)
        win32cred = self._module(target)
        try:
            result = win32cred.CredRead(
                target, win32cred.CRED_TYPE_GENERIC, 0
            )
        except Exception as exc:
            not_found = getattr(win32cred, "ERROR_NOT_FOUND", 1168)
            if getattr(exc, "winerror", None) == not_found or (
                getattr(exc, "args", ()) and exc.args[0] == not_found
            ):
                return None
            raise CredentialStoreError(
                _credential_error_message(
                    "read", target, exc, win32cred_imported=True
                )
            ) from exc
        blob = result.get("CredentialBlob", b"")
        if isinstance(blob, bytes):
            if not blob:
                return ""
            for encoding in ("utf-16-le", "utf-8"):
                try:
                    return blob.decode(encoding).rstrip("\x00")
                except UnicodeDecodeError:
                    continue
            return blob.decode(errors="replace").rstrip("\x00")
        return str(blob)

    def write(self, profile_id: str, username: str, password: str) -> None:
        target = credential_target(profile_id)
        win32cred = self._module(target)
        # PyWin32's PyCREDENTIAL contract expects CredentialBlob as PyUnicode
        # and performs the Windows UTF-16 conversion itself. Passing pre-encoded
        # bytes causes CredWrite to fail on current PyWin32 builds.
        secret = str(password or "")
        try:
            win32cred.CredWrite(
                {
                    "Type": win32cred.CRED_TYPE_GENERIC,
                    "TargetName": target,
                    "UserName": str(username or ""),
                    "CredentialBlob": secret,
                    "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
                    "Comment": "SlopeForge PostgreSQL connection credential",
                },
                0,
            )
        except Exception as exc:
            raise CredentialStoreError(
                _credential_error_message(
                    "save", target, exc, win32cred_imported=True
                )
            ) from exc

    def delete(self, profile_id: str) -> None:
        target = credential_target(profile_id)
        win32cred = self._module(target)
        try:
            win32cred.CredDelete(
                target, win32cred.CRED_TYPE_GENERIC, 0
            )
        except Exception as exc:
            not_found = getattr(win32cred, "ERROR_NOT_FOUND", 1168)
            if getattr(exc, "winerror", None) == not_found or (
                getattr(exc, "args", ()) and exc.args[0] == not_found
            ):
                return
            raise CredentialStoreError(
                _credential_error_message(
                    "remove", target, exc, win32cred_imported=True
                )
            ) from exc


def credential_runtime_smoke_test() -> None:
    """Exercise the frozen Credential Manager present-credential path safely."""
    profile_id = f"runtime-smoke-{uuid4()}"
    store = WindowsCredentialStore()
    # A random, process-local test credential ensures CredRead constructs the
    # same result type as an existing user profile. It is always deleted and
    # never logged or persisted in SlopeForge metadata.
    secret = str(uuid4())
    try:
        store.write(profile_id, "SlopeForge runtime smoke", secret)
        if store.read(profile_id) != secret:
            raise CredentialStoreError(
                "Credential Manager runtime smoke test did not read the temporary credential."
            )
    finally:
        store.delete(profile_id)


class SessionOnlyCredentialStore:
    """Non-Windows fallback: secrets live only for this process and are never written."""

    def __init__(self):
        self._values: dict[str, str] = {}

    def read(self, profile_id: str) -> str | None:
        return self._values.get(str(profile_id))

    def write(self, profile_id: str, username: str, password: str) -> None:
        del username
        self._values[str(profile_id)] = str(password or "")

    def delete(self, profile_id: str) -> None:
        self._values.pop(str(profile_id), None)


@dataclass
class MemoryCredentialStore:
    """Deterministic credential backend for tests."""

    values: dict[str, str] = field(default_factory=dict)

    def read(self, profile_id: str) -> str | None:
        return self.values.get(str(profile_id))

    def write(self, profile_id: str, username: str, password: str) -> None:
        del username
        self.values[str(profile_id)] = str(password or "")

    def delete(self, profile_id: str) -> None:
        self.values.pop(str(profile_id), None)


def default_credential_store() -> CredentialStore:
    if sys.platform == "win32":
        return WindowsCredentialStore()
    return SessionOnlyCredentialStore()
