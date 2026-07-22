"""Загрузка и валидация конфигурации из окружения (.env)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from croniter import croniter
from dotenv import load_dotenv

from .rules import Rule, RulesError, load_rules

logger = logging.getLogger(__name__)

DEFAULT_RULES_FILE = "rules.yaml"
# Каждую минуту — как прежний интервал по умолчанию в 60 секунд.
DEFAULT_CRON = "* * * * *"


class ConfigError(RuntimeError):
    """Ошибка конфигурации: не заданы или некорректны переменные окружения."""


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_cron(name: str, default: str) -> str:
    raw = (os.getenv(name) or "").strip() or default
    if not croniter.is_valid(raw):
        raise ConfigError(
            f"{name} должен быть cron-выражением вида «мин час день месяц день_недели», "
            f"получено: {raw!r}. Примеры: «*/5 * * * *» — каждые 5 минут, "
            f"«0 * * * *» — в начале каждого часа, «0 9 * * 1-5» — в 9:00 по будням."
        )
    return raw


DEFAULT_ENV_FILE = "secrets.env"


def _load_extra_env_file() -> None:
    """Подгрузить дополнительный env-файл, если включён LOAD_ENV_FILE.

    Путь берётся из ENV_FILE (по умолчанию secrets.env). Значения из файла
    переопределяют уже заданные переменные (override=True). Если флаг включён,
    но файла нет — просто пропускаем (значения возьмутся из .env/окружения).
    """
    if not _get_bool("LOAD_ENV_FILE", False):
        return

    path = (os.getenv("ENV_FILE") or "").strip() or DEFAULT_ENV_FILE
    if not os.path.isfile(path):
        logger.warning("LOAD_ENV_FILE включён, но файл ENV_FILE не найден — пропускаю: %r", path)
        return
    load_dotenv(path, override=True)


def _require(name: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise ConfigError(f"Обязательная переменная окружения {name} не задана")
    return value.strip()


@dataclass(frozen=True)
class Config:
    base_url: str
    pat: str | None
    username: str | None
    password: str | None
    verify_ssl: bool
    ca_cert_dir: str | None

    rules: tuple[Rule, ...]

    cron: str
    log_level: str
    dry_run: bool

    @classmethod
    def load(cls, rules_file: str | None = None) -> "Config":
        # load_dotenv не перезаписывает уже выставленные переменные окружения,
        # что удобно при запуске в контейнере/CI, где значения приходят извне.
        load_dotenv()

        # Опционально: подтянуть значения из отдельного файла (напр. с секретами).
        # Включается флагом LOAD_ENV_FILE; путь берётся из ENV_FILE. В отличие от
        # основного .env, этот файл имеет приоритет — override=True переопределяет
        # уже заданные переменные (в т.ч. пришедшие из .env и окружения).
        _load_extra_env_file()

        base_url = _require("CONFLUENCE_BASE_URL").rstrip("/")

        pat = (os.getenv("CONFLUENCE_PAT") or "").strip() or None
        username = (os.getenv("CONFLUENCE_USERNAME") or "").strip() or None
        password = (os.getenv("CONFLUENCE_PASSWORD") or "").strip() or None

        if not pat and not (username and password):
            raise ConfigError(
                "Не задана авторизация: укажите CONFLUENCE_PAT либо "
                "пару CONFLUENCE_USERNAME + CONFLUENCE_PASSWORD"
            )

        # Приоритет: аргумент CLI → переменная окружения → значение по умолчанию.
        path = rules_file or (os.getenv("RULES_FILE") or "").strip() or DEFAULT_RULES_FILE
        try:
            rules = load_rules(path)
        except RulesError as exc:
            raise ConfigError(str(exc)) from exc

        return cls(
            base_url=base_url,
            pat=pat,
            username=username,
            password=password,
            verify_ssl=_get_bool("CONFLUENCE_VERIFY_SSL", True),
            ca_cert_dir=(os.getenv("CONFLUENCE_CA_CERT_DIR") or "").strip() or None,
            rules=tuple(rules),
            cron=_get_cron("CRON_SCHEDULE", DEFAULT_CRON),
            log_level=(os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
            dry_run=_get_bool("DRY_RUN", False),
        )