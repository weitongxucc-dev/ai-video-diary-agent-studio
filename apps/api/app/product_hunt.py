from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import Settings
from app.sources import TopicSourceItem


@dataclass(frozen=True)
class ProductHuntPost:
    id: str
    name: str
    tagline: str
    url: str
    votes_count: int


class ProductHuntConfigurationError(RuntimeError):
    pass


class ProductHuntResponseError(RuntimeError):
    pass


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
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            response = client.post(self._settings.product_hunt_api_url, json={"query": query}, headers=headers)

        try:
            response.raise_for_status()
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
            )
            for post in self.fetch_posts(limit=limit)
        ]
