"""Расписания и флаги: приоритет переменных и обратная совместимость."""

from __future__ import annotations

import pytest

from confluence_label_bot.config import DEFAULT_CRON, Config, ConfigError


def test_без_переменных_оба_расписания_по_умолчанию(env, rules_yaml):
    rules_yaml()
    config = Config.load()

    assert config.move_cron == DEFAULT_CRON
    assert config.mentions_cron == DEFAULT_CRON


def test_старое_имя_cron_schedule_продолжает_работать(env, rules_yaml):
    """CRON_SCHEDULE уже задан в развёрнутых установках — ломать его нельзя."""
    rules_yaml()
    env.setenv("CRON_SCHEDULE", "0 9 * * 1-5")

    config = Config.load()

    assert config.move_cron == "0 9 * * 1-5"
    assert config.mentions_cron == "0 9 * * 1-5"


def test_move_cron_schedule_приоритетнее_старого_имени(env, rules_yaml):
    rules_yaml()
    env.setenv("CRON_SCHEDULE", "0 9 * * 1-5")
    env.setenv("MOVE_CRON_SCHEDULE", "*/5 * * * *")

    assert Config.load().move_cron == "*/5 * * * *"


def test_расписание_упоминаний_наследует_расписание_переносов(env, rules_yaml):
    rules_yaml()
    env.setenv("MOVE_CRON_SCHEDULE", "*/5 * * * *")

    assert Config.load().mentions_cron == "*/5 * * * *"


def test_расписания_задаются_независимо(env, rules_yaml):
    rules_yaml()
    env.setenv("MOVE_CRON_SCHEDULE", "*/5 * * * *")
    env.setenv("MENTIONS_CRON_SCHEDULE", "0 * * * *")

    config = Config.load()

    assert config.move_cron == "*/5 * * * *"
    assert config.mentions_cron == "0 * * * *"


def test_опечатка_в_расписании_называет_переменную(env, rules_yaml):
    rules_yaml()
    env.setenv("MENTIONS_CRON_SCHEDULE", "каждый вторник")

    with pytest.raises(ConfigError, match="MENTIONS_CRON_SCHEDULE"):
        Config.load()


def test_dry_run_наследуется_задачей_упоминаний(env, rules_yaml):
    rules_yaml()
    env.setenv("DRY_RUN", "true")

    config = Config.load()

    assert config.dry_run is True
    assert config.mentions_dry_run is True


def test_dry_run_переопределяется_для_упоминаний(env, rules_yaml):
    """Переносы вживую, сбор упоминаний в пробном прогоне — режим раскатки."""
    rules_yaml()
    env.setenv("DRY_RUN", "false")
    env.setenv("MENTIONS_DRY_RUN", "true")

    config = Config.load()

    assert config.dry_run is False
    assert config.mentions_dry_run is True
