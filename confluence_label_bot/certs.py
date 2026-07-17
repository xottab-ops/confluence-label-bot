"""Сборка CA-бандла из папки с корневыми сертификатами.

Confluence Server/DC часто подписан внутренним корневым CA, которого нет в
системном/`certifi` хранилище. Собираем единый PEM-бандл из всех сертификатов
указанной папки (плюс, при наличии, `certifi`) и передаём его в requests.
"""

from __future__ import annotations

import logging
import os
import ssl
import tempfile

logger = logging.getLogger(__name__)

# Расширения файлов, которые считаем сертификатами.
_CERT_EXTS = {".pem", ".crt", ".cer", ".der", ".cert"}


def _to_pem(raw: bytes) -> str | None:
    """Привести содержимое файла сертификата к PEM. Поддерживает PEM и DER."""
    text = raw.lstrip()
    if text.startswith(b"-----BEGIN"):
        return raw.decode("ascii", errors="ignore")
    # Иначе пробуем как DER.
    try:
        return ssl.DER_cert_to_PEM_cert(raw)
    except (ValueError, ssl.SSLError):
        return None


def build_ca_bundle(cert_dir: str, *, include_certifi: bool = True) -> str:
    """Собрать все сертификаты из `cert_dir` в один PEM-бандл.

    Возвращает путь к созданному временному файлу-бандлу.
    """
    if not os.path.isdir(cert_dir):
        raise FileNotFoundError(f"Папка с сертификатами не найдена: {cert_dir}")

    chunks: list[str] = []

    if include_certifi:
        try:
            import certifi

            with open(certifi.where(), encoding="ascii") as fh:
                chunks.append(fh.read())
        except Exception:  # noqa: BLE001 — certifi необязателен
            logger.debug("certifi недоступен, использую только сертификаты из папки")

    found = 0
    for name in sorted(os.listdir(cert_dir)):
        path = os.path.join(cert_dir, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in _CERT_EXTS:
            continue
        with open(path, "rb") as fh:
            pem = _to_pem(fh.read())
        if pem is None:
            logger.warning("Файл %s не распознан как сертификат, пропуск", name)
            continue
        chunks.append(pem if pem.endswith("\n") else pem + "\n")
        found += 1

    if found == 0:
        raise FileNotFoundError(
            f"В папке {cert_dir} не найдено сертификатов "
            f"(ожидаются файлы с расширениями {sorted(_CERT_EXTS)})"
        )

    fd, bundle_path = tempfile.mkstemp(prefix="confluence-ca-", suffix=".pem")
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write("\n".join(chunks))

    logger.info("Собран CA-бандл из %d сертификат(ов): %s", found, bundle_path)
    return bundle_path