"""Загрузка и валидация конфигурации из окружения (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .rules import Rule, RulesError, load_rules

DEFAULT_RULES_FILE = "rules.yaml"


class ConfigError(RuntimeError):
    """Ошибка конфигурации: не заданы или некорректны переменные окружения."""


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должен быть целым числом, получено: {raw!r}") from exc


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

    poll_interval_seconds: int
    log_level: str
    dry_run: bool

    @classmethod
    def load(cls, rules_file: str | None = None) -> "Config":
        # load_dotenv не перезаписывает уже выставленные переменные окружения,
        # что удобно при запуске в контейнере/CI, где значения приходят извне.
        load_dotenv()

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
            poll_interval_seconds=_get_int("POLL_INTERVAL_SECONDS", 60),
            log_level=(os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
            dry_run=_get_bool("DRY_RUN", False),
        )