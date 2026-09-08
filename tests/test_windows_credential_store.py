from __future__ import annotations

import pytest

from app.credential_store import (
    CredentialStoreError,
    WindowsCredentialStore,
    credential_runtime_smoke_test,
    credential_target,
)


class FakeWin32Cred:
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    def __init__(self):
        self.writes = []
        self.value = None

    def CredWrite(self, credential, flags):
        self.writes.append((credential, flags))
        if not isinstance(credential["CredentialBlob"], str):
            raise TypeError("CredentialBlob must be PyUnicode")
        self.value = credential

    def CredRead(self, target, credential_type, flags):
        if self.value is None:
            exc = RuntimeError("not found")
            exc.winerror = self.ERROR_NOT_FOUND
            raise exc
        assert target == self.value["TargetName"]
        assert credential_type == self.CRED_TYPE_GENERIC
        assert flags == 0
        return {
            **self.value,
            "CredentialBlob": self.value["CredentialBlob"].encode("utf-16-le"),
        }

    def CredDelete(self, target, credential_type, flags):
        self.value = None


def test_windows_credential_write_passes_unicode_blob_and_round_trips(monkeypatch):
    fake = FakeWin32Cred()
    monkeypatch.setattr(WindowsCredentialStore, "_module", staticmethod(lambda target: fake))
    store = WindowsCredentialStore()

    store.write("profile-123", "postgres", "p@ss:/word")

    credential, flags = fake.writes[-1]
    assert flags == 0
    assert credential["TargetName"] == credential_target("profile-123")
    assert credential["UserName"] == "postgres"
    assert credential["CredentialBlob"] == "p@ss:/word"
    assert isinstance(credential["CredentialBlob"], str)
    assert store.read("profile-123") == "p@ss:/word"


def test_windows_credential_error_exposes_safe_windows_code(monkeypatch):
    class FailingWin32Cred(FakeWin32Cred):
        def CredWrite(self, credential, flags):
            exc = RuntimeError("policy rejected credential")
            exc.winerror = 1312
            raise exc

    fake = FailingWin32Cred()
    monkeypatch.setattr(WindowsCredentialStore, "_module", staticmethod(lambda target: fake))

    with pytest.raises(CredentialStoreError, match=r"Windows error 1312"):
        WindowsCredentialStore().write("profile-123", "postgres", "secret")


def test_windows_credential_read_error_reports_safe_runtime_context(monkeypatch):
    class FailingWin32Cred(FakeWin32Cred):
        def CredRead(self, target, credential_type, flags):
            exc = RuntimeError("credential API failed")
            exc.winerror = 126
            raise exc

    fake = FailingWin32Cred()
    monkeypatch.setattr(WindowsCredentialStore, "_module", staticmethod(lambda target: fake))

    with pytest.raises(CredentialStoreError) as raised:
        WindowsCredentialStore().read("profile-123")

    message = str(raised.value)
    assert "exception: RuntimeError" in message
    assert "Windows error 126" in message
    assert "credential target: SlopeForge/PostgreSQL/profile-123" in message
    assert "credential read: no" in message
    assert "frozen:" in message
    assert "sys.executable:" in message
    assert "win32cred import: yes" in message
    assert "credential API failed" not in message


def test_credential_runtime_smoke_test_round_trips_and_removes_temporary_credential(monkeypatch):
    fake = FakeWin32Cred()
    targets = []

    def module(target):
        targets.append(target)
        return fake

    monkeypatch.setattr(WindowsCredentialStore, "_module", staticmethod(module))

    credential_runtime_smoke_test()

    assert len(targets) == 3
    assert all(target.startswith("SlopeForge/PostgreSQL/runtime-smoke-") for target in targets)
    assert fake.value is None
