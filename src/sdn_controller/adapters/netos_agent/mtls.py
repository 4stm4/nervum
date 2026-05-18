"""TLS / mTLS обвязка для ``HttpAgentClient`` (M9 — SDN-029).

Что входит в M9:

* ``build_mtls_ssl_context`` — собирает ``ssl.SSLContext`` из настроек
  (CA-бандл + клиентская пара). Возвращает ``None``, если mTLS выключен.
* ``compute_certificate_thumbprint`` — SHA-256 hex от DER-кодированного
  сертификата. Используется enrollment-flow'ом, чтобы агент мог
  отправить контроллеру серверный thumbprint.

Что не входит и приедет позже:

* Полноценный pin-check серверного сертификата по ``Node.tls_thumbprint``
  на каждом запросе. Это требует кастомного ``verify_callback`` через
  ``ssl.SSLObject`` или собственного TLS-handshake'а вокруг httpx —
  для MVP мы доверяем CA-бандлу и логируем thumbprint в сторону
  оператора. Pin-check переедет в Milestone 11 (operability).
* Ротация сертификатов: M9 фиксирует структуру (PEM-пути в настройках),
  ротация — это смена путей + reload.
"""

from __future__ import annotations

import hashlib
import ssl
from pathlib import Path

from sdn_controller.app.config import Settings
from sdn_controller.core.value_objects.errors import ValidationError


def build_mtls_ssl_context(settings: Settings) -> ssl.SSLContext | None:
    """Сконфигурированный ``ssl.SSLContext`` для контроллер→агент mTLS.

    Возвращает ``None``, если ``agent_mtls_enabled=False`` — вызывающий
    использует обычный httpx-клиент без TLS.

    Бросает ``ValidationError``, если включён, но пути не настроены или
    файлы не открываются — это «failed-closed» по дизайну, лучше упасть
    на старте, чем тихо уходить по plaintext'у.
    """
    if not settings.agent_mtls_enabled:
        return None

    paths = {
        "agent_mtls_ca_cert_path": settings.agent_mtls_ca_cert_path,
        "agent_mtls_client_cert_path": settings.agent_mtls_client_cert_path,
        "agent_mtls_client_key_path": settings.agent_mtls_client_key_path,
    }
    missing = [name for name, value in paths.items() if not value]
    if missing:
        raise ValidationError("agent_mtls_enabled requires: " + ", ".join(sorted(missing)))
    for name, value in paths.items():
        if value is None:
            continue  # уже проверено выше
        if not Path(value).is_file():
            raise ValidationError(f"{name}: file not found: {value}")

    ctx = ssl.create_default_context(cafile=settings.agent_mtls_ca_cert_path)
    ctx.load_cert_chain(
        certfile=settings.agent_mtls_client_cert_path or "",
        keyfile=settings.agent_mtls_client_key_path,
    )
    # Контроллер ходит к агенту по предсказуемому DNS-имени или IP —
    # CN/SAN мы проверяем стандартом ssl, а pin по thumbprint'у
    # выполнит вышестоящий слой (см. docstring модуля).
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def compute_certificate_thumbprint(cert_der: bytes) -> str:
    """SHA-256 hex от DER-байтов сертификата.

    Это идентификатор, который агент шлёт контроллеру при enrollment'е
    в поле ``tls_thumbprint``. Контроллер сохраняет его на ``Node`` и
    использует как pinned identity.
    """
    return hashlib.sha256(cert_der).hexdigest()


__all__ = ["build_mtls_ssl_context", "compute_certificate_thumbprint"]
