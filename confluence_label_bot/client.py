"""Тонкий клиент к REST API Confluence Server / Data Center."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from http.cookiejar import DefaultCookiePolicy
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
    # Полный путь предков от корня к непосредственному родителю.
    ancestor_ids: tuple[str, ...]

    @property
    def parent_id(self) -> str | None:
        return self.ancestor_ids[-1] if self.ancestor_ids else None

    def is_under(self, page_id: str) -> bool:
        """Лежит ли страница в поддереве page_id (на любой глубине)."""
        return page_id in self.ancestor_ids


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
        session.headers.update(
            {
                "Accept": "application/json",
                # Ответы REST не должны браться из кеша прокси между ботом и Confluence.
                "Cache-Control": "no-cache",
            }
        )

        # Не принимать куки. Confluence на первый же запрос отдаёт JSESSIONID, и
        # дальше авторизует по сессии, а не по токену: стоит той сессии протухнуть
        # или привязаться к анониму — и бот начинает видеть лишь часть страниц,
        # хотя PAT валиден. Без кук каждый запрос аутентифицируется заново.
        session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))

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
    def _to_page(data: dict[str, Any], ancestor_ids: Sequence[str] | None = None) -> Page:
        if ancestor_ids is None:
            # ancestors приходят от корня к непосредственному родителю.
            ancestor_ids = [str(a["id"]) for a in (data.get("ancestors") or [])]
        return Page(
            id=str(data["id"]),
            title=data.get("title", ""),
            version=int(data.get("version", {}).get("number", 0)),
            space_key=(data.get("space") or {}).get("key", ""),
            ancestor_ids=tuple(ancestor_ids),
        )

    def _iter_paged(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Пройти все страницы постраничной выдачи.

        Признак конца — отсутствие ссылки `_links.next`, а не то, что результатов
        пришло меньше запрошенного: порция бывает неполной и в середине выдачи —
        сервер вправе урезать limit до своего максимума.
        """
        start = 0
        limit = 50
        while True:
            data = self._request("GET", path, params={**params, "limit": limit, "start": start})
            results = data.get("results", [])
            yield from results

            if not (data.get("_links") or {}).get("next"):
                return

            # Шагаем на фактический limit из ответа: запрошенный сервер мог урезать.
            step = int(data.get("limit") or 0) or len(results)
            if step <= 0:
                logger.warning(
                    "Пагинация %s: сервер обещает следующую страницу, но порция пуста "
                    "(start=%d). Прерываю, иначе цикл не кончится.",
                    path,
                    start,
                )
                return
            start += step

    @staticmethod
    def _labels_of(data: dict[str, Any]) -> set[str]:
        results = ((data.get("metadata") or {}).get("labels") or {}).get("results") or []
        return {str(label.get("name", "")) for label in results}

    def _walk_subtree(
        self, root_id: str
    ) -> Iterator[tuple[dict[str, Any], tuple[str, ...]]]:
        """Обойти поддерево root_id, спускаясь по /child/page.

        Отдаёт пару (страница, её путь предков). Путь строится по ходу спуска,
        поэтому `expand=ancestors` не нужен — на /descendant/page он и приводил
        к HTTP 500.
        """
        # (id родителя, путь предков его детей)
        queue: list[tuple[str, tuple[str, ...]]] = [(root_id, (root_id,))]
        seen: set[str] = {root_id}

        while queue:
            parent_id, path = queue.pop()
            for item in self._iter_paged(
                f"/content/{parent_id}/child/page",
                {"expand": "version,space,metadata.labels"},
            ):
                page_id = str(item["id"])
                if page_id in seen:  # страховка от зацикливания
                    continue
                seen.add(page_id)
                yield item, path
                queue.append((page_id, path + (page_id,)))

    # ── публичное ───────────────────────────────────────────────────────────
    def find_pages_with_labels_under(
        self,
        *,
        ancestor_id: str,
        labels: Sequence[str],
        space_key: str | None = None,
    ) -> list[Page]:
        """Страницы в поддереве ancestor_id с любым из labels (любая глубина).

        Обходит дерево по /child/page и сверяет лейблы сам, вместо поиска по CQL.
        Так выборка читается из базы, а не из Lucene-индекса: индекс обновляется
        асинхронно, а в кластере живёт на каждой ноде свой, поэтому CQL умеет
        молча возвращать неполный результат.
        """
        wanted = {label.lower() for label in labels}
        pages: list[Page] = []
        total = 0

        for item, ancestor_ids in self._walk_subtree(ancestor_id):
            total += 1
            if not {name.lower() for name in self._labels_of(item)} & wanted:
                continue
            page = self._to_page(item, ancestor_ids)
            if space_key and page.space_key != space_key:
                continue
            pages.append(page)

        logger.debug(
            "Поддерево %s: страниц всего %d, с лейблами (%s) — %d",
            ancestor_id,
            total,
            ", ".join(labels),
            len(pages),
        )
        return pages

    def get_current_user(self) -> str:
        """Под каким пользователем бот работает.

        Видимость страниц определяется правами именно этого пользователя: если
        авторизация незаметно откатилась на анонима, выборка окажется неполной.
        """
        data = self._request("GET", "/user/current")
        name = data.get("username") or data.get("accountId") or "?"
        display = data.get("displayName") or ""
        return f"{name} ({display})" if display else name

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