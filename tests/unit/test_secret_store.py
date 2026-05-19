"""Unit-тесты ``InMemorySecretStore`` / ``FernetSecretStore`` (SDN-043)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdn_controller.adapters.secret_store import (
    FernetSecretStore,
    InMemorySecretStore,
    SecretStoreUnlockError,
    generate_master_key,
)

# ---------------------------------------------------------------------------
# InMemorySecretStore
# ---------------------------------------------------------------------------


async def test_in_memory_roundtrip() -> None:
    store = InMemorySecretStore()
    await store.remember("k1", "plain-1")
    assert await store.get("k1") == "plain-1"
    assert await store.get("missing") is None
    await store.forget("k1")
    assert await store.get("k1") is None


async def test_in_memory_forget_unknown_is_noop() -> None:
    store = InMemorySecretStore()
    await store.forget("never-existed")
    assert await store.get("never-existed") is None


# ---------------------------------------------------------------------------
# FernetSecretStore
# ---------------------------------------------------------------------------


async def test_fernet_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "store.enc"
    key = generate_master_key()
    store = FernetSecretStore(path=path, master_key=key)
    await store.remember("whsub_a", "secret-a")
    await store.remember("whsub_b", "secret-b")

    assert await store.get("whsub_a") == "secret-a"
    assert await store.get("whsub_b") == "secret-b"
    assert await store.get("missing") is None


async def test_fernet_survives_restart(tmp_path: Path) -> None:
    """Сохранили на одном инстансе — прочли на другом с тем же ключом."""
    path = tmp_path / "store.enc"
    key = generate_master_key()
    first = FernetSecretStore(path=path, master_key=key)
    await first.remember("whsub_a", "secret-a")

    second = FernetSecretStore(path=path, master_key=key)
    assert await second.get("whsub_a") == "secret-a"


async def test_fernet_forget_persists(tmp_path: Path) -> None:
    path = tmp_path / "store.enc"
    key = generate_master_key()
    store = FernetSecretStore(path=path, master_key=key)
    await store.remember("whsub_a", "secret-a")
    await store.forget("whsub_a")

    reloaded = FernetSecretStore(path=path, master_key=key)
    assert await reloaded.get("whsub_a") is None


async def test_fernet_wrong_key_raises(tmp_path: Path) -> None:
    path = tmp_path / "store.enc"
    correct = generate_master_key()
    wrong = generate_master_key()
    await FernetSecretStore(path=path, master_key=correct).remember("k", "v")

    store = FernetSecretStore(path=path, master_key=wrong)
    with pytest.raises(SecretStoreUnlockError):
        await store.get("k")


async def test_fernet_file_is_chmod_600(tmp_path: Path) -> None:
    path = tmp_path / "store.enc"
    key = generate_master_key()
    store = FernetSecretStore(path=path, master_key=key)
    await store.remember("k", "v")

    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


async def test_fernet_empty_get_for_missing_file(tmp_path: Path) -> None:
    """Если файла ещё нет, ``get`` отдаёт ``None`` (не создаёт файл)."""
    path = tmp_path / "store.enc"
    key = generate_master_key()
    store = FernetSecretStore(path=path, master_key=key)
    assert await store.get("anything") is None
    assert not path.exists()
