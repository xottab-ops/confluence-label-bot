"""Тонкий клиент к REST API Confluence Server / Data Center."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

from .certs import build_ca_bundle
from .config import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Page:
    id: str
    title: str
    version: int
    space_key: str
    parent_id: str | None


class ConfluenceError(RuntimeError):
    """Ошибка обращения к Confluence API."""


class ConfluenceClient:
    """Обёртка над /rest/api для нужд бота.

    Работает с Confluence Server / Data Center. Авторизация — Bearer (PAT)
    либо Basic (username + password).
    """

    def __init__(self, config: Config) -> None:
        self._base = config.base_url
        self._api = f"{self._base}/rest/api"

        session = requests.Session()
        session.verify = self._resolve_verify(config)
        session.headers.update({"Accept": "application/json"})
        if config.pat:
            session.headers["Authorization"] = f"Bearer {config.pat}"
            logger.debug("Авторизация: Personal Access Token (Bearer)")
        else:
            session.auth = (config.username or "", config.password or "")
            logger.debug("Авторизация: Basic (username + password)")
        self._session = session

    @staticmethod
    def _resolve_verify(config: Config) -> bool | str:
        """Определить значение для requests `verify`.

        - verify_ssl=False → проверка отключена;
        - задана папка с корневыми сертификатами → путь к собранному CA-бандлу;
        - иначе → стандартная проверка по системному/`certifi` хранилищу.
        """
        if not config.verify_ssl:
            logger.warning("Проверка SSL-сертификата отключена (CONFLUENCE_VERIFY_SSL=false)")
            return False
        if config.ca_cert_dir:
            return build_ca_bundle(config.ca_cert_dir)
        return True

    # ── внутреннее ──────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = path if path.startswith("http") else f"{self._api}{path}"
        try:
            resp = self._session.request(method, url, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ConfluenceError(f"Сетевая ошибка при {method} {url}: {exc}") from exc

        if not resp.ok:
            raise ConfluenceError(
                f"{method} {url} → HTTP {resp.status_code}: {resp.text[:500]}"
            )
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    @staticmethod
    def _to_page(data: dict[str, Any]) -> Page:
        ancestors = data.get("ancestors") or []
        parent_id = str(ancestors[-1]["id"]) if ancestors else None
        return Page(
            id=str(data["id"]),
            title=data.get("title", ""),
            version=int(data.get("version", {}).get("number", 0)),
            space_key=(data.get("space") or {}).get("key", ""),
            parent_id=parent_id,
        )

    # ── публичное ───────────────────────────────────────────────────────────
    def find_pages_with_label_under(
        self, *, space_key: str, ancestor_id: str, label: str
    ) -> list[Page]:
        """Все страницы поддерева ancestor_id с указанным лейблом (любая глубина).

        Использует CQL: оператор `ancestor` находит потомков на любом уровне.
        """
        cql = (
            f'space="{space_key}" and type=page '
            f'and label="{label}" and ancestor={ancestor_id}'
        )
        pages: list[Page] = []
        start = 0
        limit = 50
        while True:
            data = self._request(
                "GET",
                "/content/search",
                params={
                    "cql": cql,
                    "limit": limit,
                    "start": start,
                    "expand": "version,space,ancestors",
                },
            )
            results = data.get("results", [])
            pages.extend(self._to_page(item) for item in results)
            if len(results) < limit:
                break
            start += limit
        return pages

    def get_page(self, page_id: str) -> Page:
        data = self._request(
            "GET",
            f"/content/{page_id}",
            params={"expand": "version,space,ancestors"},
        )
        return self._to_page(data)

    def move_page(self, page: Page, new_parent_id: str) -> None:
        """Сменить родителя страницы (перенос вместе со всем поддеревом).

        Реализовано через PUT /content/{id} с новым `ancestors` и версией +1.
        """
        body = {
            "id": page.id,
            "type": "page",
            "title": page.title,
            "space": {"key": page.space_key},
            "ancestors": [{"id": str(new_parent_id)}],
            "version": {"number": page.version + 1},
        }
        self._request(
            "PUT",
            f"/content/{page.id}",
            json=body,
            headers={"Content-Type": "application/json"},
        )