"""Общие приспособления для тестов."""

from __future__ import annotations

import pytest

# Всё, что читает Config.load. Тест должен видеть только то, что выставил сам,
# иначе окружение разработчика меняет результат.
_ENV_VARS = (
    "CONFLUENCE_BASE_URL",
    "CONFLUENCE_PAT",
    "CONFLUENCE_USERNAME",
    "CONFLUENCE_PASSWORD",
    "CONFLUENCE_VERIFY_SSL",
    "CONFLUENCE_CA_CERT_DIR",
    "CONFLUENCE_QUERY_DELAY",
    "CONFLUENCE_MAX_RETRIES",
    "CONFLUENCE_RETRY_MAX_WAIT",
    "RULES_FILE",
    "CRON_SCHEDULE",
    "MOVE_CRON_SCHEDULE",
    "MENTIONS_CRON_SCHEDULE",
    "LOG_LEVEL",
    "DRY_RUN",
    "MENTIONS_DRY_RUN",
    "GRAPH_CRON_SCHEDULE",
    "GRAPH_DRY_RUN",
    "MOVE_ENABLED",
    "MENTIONS_ENABLED",
    "GRAPH_ENABLED",
    "LOAD_ENV_FILE",
    "ENV_FILE",
    "HEALTH_PORT",
    "HEALTH_LIVENESS_TIMEOUT",
)

MINIMAL_RULES = """
rules:
  - name: r1
    source: "111"
    labels: ready
    target: "222"
"""


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Чистое окружение в пустом каталоге, с минимумом обязательных переменных.

    chdir в tmp_path обязателен: Config.load вызывает load_dotenv(), который
    ищет .env по родительским каталогам, и из каталога репозитория он
    подхватил бы настоящий .env разработчика.
    """
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://confluence.example.com")
    monkeypatch.setenv("CONFLUENCE_PAT", "token")
    monkeypatch.setenv("CONFLUENCE_QUERY_DELAY", "0")
    return monkeypatch


@pytest.fixture
def rules_yaml(tmp_path):
    """Записать rules.yaml в рабочий каталог теста и вернуть путь."""

    def write(text: str = MINIMAL_RULES) -> str:
        path = tmp_path / "rules.yaml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    return write
