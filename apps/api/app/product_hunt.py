from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import Settings
from app.source_sync import SourceBoundaryError, source_error_from_http_status
from app.source_types import TopicSourceItem


@dataclass(frozen=True)
class ProductHuntPost:
    id: str
    name: str
    tagline: str
    url: str
    votes_count: int


class ProductHuntConfigurationError(SourceBoundaryError):
    def __init__(self, message: str) -> None:
        super().__init__(
            "authentication_required",
            message,
            repair_action="Configure a valid Product Hunt token before syncing.",
        )


class ProductHuntResponseError(SourceBoundaryError):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "temporary_failure",
        impact: str | None = None,
        repair_action: str | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(
            kind,
            message,
            impact=impact,
            repair_action=repair_action,
            retryable=retryable,
        )


class ProductHuntClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._timeout = timeout

    def fetch_posts(self, limit: int = 10) -> list[ProductHuntPost]:
        if not self._settings.product_hunt_token:
            raise ProductHuntConfigurationError("PRODUCT_HUNT_TOKEN is not configured")
        query = f"""
        query {{
          posts(first: {limit}) {{
            edges {{
              node {{
                id
                name
                tagline
                url
                votesCount
              }}
            }}
          }}
        }}
        """
        headers = {"Authorization": f"Bearer {self._settings.product_hunt_token}"}
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                response = client.post(self._settings.product_hunt_api_url, json={"query": query}, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            semantic = source_error_from_http_status(
                exc.response.status_code, "Product Hunt"
            )
            raise ProductHuntResponseError(
                str(semantic),
                kind=semantic.kind,
                impact=semantic.impact,
                repair_action=semantic.repair_action,
                retryable=semantic.retryable,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProductHuntResponseError(
                "Product Hunt request failed",
                repair_action="Check the network and Product Hunt service, then retry.",
            ) from exc

        try:
            edges = response.json()["data"]["posts"]["edges"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProductHuntResponseError("Product Hunt did not return valid posts") from exc

        posts: list[ProductHuntPost] = []
        for edge in edges:
            node = edge["node"]
            posts.append(
                ProductHuntPost(
                    id=str(node["id"]),
                    name=str(node["name"]),
                    tagline=str(node["tagline"]),
                    url=str(node["url"]),
                    votes_count=int(node["votesCount"]),
                )
            )
        return posts

    def fetch_source_items(self, limit: int = 10) -> list[TopicSourceItem]:
        return [
            TopicSourceItem(
                source_type="Product Hunt",
                title=post.name,
                ref=f"producthunt:{post.id}",
                excerpt=f"{post.tagline}\nVotes: {post.votes_count}\nURL: {post.url}",
                source_url=post.url,
            )
            for post in self.fetch_posts(limit=limit)
        ]
