"""Реализации ``SecretStore`` (SDN-043).

* ``InMemorySecretStore`` — простой dict под мьютексом. Не выживает
  рестарт. Дев и тесты.
* ``FernetSecretStore`` — JSON-файл, зашифрованный Fernet'ом по
  мастер-ключу из ``SDN_SECRET_STORE_KEY``. Файл создаётся с
  ``chmod 600``; директория должна быть с такими же ограниченными
  правами. Это default для prod без external secret manager'а.

Fernet — это AES-128-CBC + HMAC-SHA256 + base64; нам этого достаточно
для «небольшое количество коротких секретов, переживающих рестарт
процесса». Полноценные KMS/Vault — отдельные адаптеры будущего.
"""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import structlog
from cryptography.fernet import Fernet, InvalidToken

_log = structlog.get_logger(__name__)


class InMemorySecretStore:
    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}
        self._lock = anyio.Lock()

    async def remember(self, key: str, plaintext: str) -> None:
        async with self._lock:
            self._secrets[key] = plaintext

    async def get(self, key: str) -> str | None:
        async with self._lock:
            return self._secrets.get(key)

    async def forget(self, key: str) -> None:
        async with self._lock:
            self._secrets.pop(key, None)


class FernetSecretStore:
    """JSON-файл, зашифрованный Fernet'ом.

    Структура внутри: ``{"<key>": "<plaintext>", ...}``. При каждом
    ``remember``/``forget`` файл переписывается целиком — это просто и
    атомарно через ``replace``-rename. Для текущих объёмов (десятки
    подписок) этого хватает с запасом.

    ``key`` — это любой строковый идентификатор; мы используем id
    подписки (``whsub_*``). Сам plaintext не парсится — это
    непрозрачная строка.
    """

    def __init__(self, *, path: str | Path, master_key: str) -> None:
        self._path = Path(path)
        self._fernet = Fernet(master_key.encode("utf-8"))
        self._lock = anyio.Lock()

    async def _read_all(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        raw = self._path.read_bytes()
        if not raw:
            return {}
        try:
            decrypted = self._fernet.decrypt(raw)
        except InvalidToken as exc:
            raise SecretStoreUnlockError(
                f"failed to decrypt secret store at {self._path}: "
                f"wrong master key or file corrupted",
            ) from exc
        loaded = json.loads(decrypted.decode("utf-8"))
        if not isinstance(loaded, dict):
            raise SecretStoreCorruptError(
                f"secret store at {self._path} does not decode to a dict",
            )
        # Defensive: filter out non-string values.
        return {str(k): str(v) for k, v in loaded.items()}

    async def _write_all(self, data: dict[str, str]) -> None:
        body = json.dumps(data, sort_keys=True).encode("utf-8")
        ciphertext = self._fernet.encrypt(body)
        # Atomic write: пишем в *.tmp, потом rename.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(ciphertext)
        tmp.chmod(0o600)
        tmp.replace(self._path)

    async def remember(self, key: str, plaintext: str) -> None:
        async with self._lock:
            data = await self._read_all()
            data[key] = plaintext
            await self._write_all(data)

    async def get(self, key: str) -> str | None:
        async with self._lock:
            data = await self._read_all()
            return data.get(key)

    async def forget(self, key: str) -> None:
        async with self._lock:
            data = await self._read_all()
            if data.pop(key, None) is None:
                return
            await self._write_all(data)


class SecretStoreError(Exception):
    """Базовая ошибка SecretStore-адаптеров."""


class SecretStoreUnlockError(SecretStoreError):
    """Не получилось расшифровать файл — wrong key или corruption."""


class SecretStoreCorruptError(SecretStoreError):
    """Файл расшифровался, но содержимое не выглядит правильно."""


def generate_master_key() -> str:
    """Helper для ops: сгенерить master key одной строкой.

    Применяется только в bootstrap-скриптах / CLI ``sdn-controller
    keygen``. В production-коде не дёргается.
    """
    return Fernet.generate_key().decode("ascii")


__all__ = [
    "FernetSecretStore",
    "InMemorySecretStore",
    "SecretStoreCorruptError",
    "SecretStoreError",
    "SecretStoreUnlockError",
    "generate_master_key",
]
