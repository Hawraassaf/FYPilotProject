"""
FYPilot Retrieval Service

RTA-2:
Central retrieval layer for AI agents.

Flow:

Agent
  |
  ↓
RetrievalService
  |
  ↓
Tavily Web Search
  |
  ↓
Sources(title,url,snippet)
  |
  ↓
LLM grounding

Rules:
- groundedInLiveData = True ONLY when real URLs are returned.
- Never fabricate evidence.
"""

import os
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests


logger = logging.getLogger("fypilot-retrieval")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class RetrievalService:

    def __init__(self):
        self.tavily_api_key = os.getenv("TAVILY_API_KEY")

        self.endpoint = (
            os.getenv(
                "TAVILY_ENDPOINT",
                "https://api.tavily.com/search"
            )
        )

        self.last_error: str | None = None
        self.last_search_time: str | None = None


    def search(
        self,
        query: str,
        *,
        k: int = 5,
    ) -> list[SearchResult]:

        self.last_error = None
        self.last_search_time = (
            datetime.now(timezone.utc)
            .isoformat()
        )


        if not self.tavily_api_key:
            self.last_error = (
                "TAVILY_API_KEY is missing"
            )

            logger.warning(
                self.last_error
            )

            return []


        try:

            response = requests.post(
                self.endpoint,

                headers={
                    "Authorization":
                        f"Bearer {self.tavily_api_key}",

                    "Content-Type":
                        "application/json",
                },

                json={

                    "query": query,

                    "search_depth":
                        "advanced",

                    "max_results":
                        k,

                    "include_answer":
                        False,

                    "include_raw_content":
                        False,
                },

                timeout=30,
            )


            response.raise_for_status()


            payload = response.json()


            results = []

            seen_urls = set()


            for item in payload.get(
                "results",
                []
            ):

                title = (
                    str(
                        item.get(
                            "title",
                            ""
                        )
                    )
                    .strip()
                )


                url = (
                    str(
                        item.get(
                            "url",
                            ""
                        )
                    )
                    .strip()
                )


                snippet = (
                    str(
                        item.get(
                            "content",
                            ""
                        )
                    )
                    .strip()
                )


                # Remove duplicates
                if url in seen_urls:
                    continue


                # Ignore bad results
                if not title or not url:
                    continue


                if not snippet:
                    continue


                seen_urls.add(url)


                results.append(
                    SearchResult(
                        title=title,

                        url=url,

                        snippet=snippet[:800],
                    )
                )


            return results[:k]


        except requests.Timeout:

            self.last_error = (
                "Tavily request timeout"
            )

            logger.error(
                self.last_error
            )

            return []


        except Exception as ex:

            self.last_error = str(ex)

            logger.exception(
                "Retrieval failed"
            )

            return []



def build_grounding_block(
    sources: list[SearchResult]
) -> str:


    if not sources:

        return """

No verified live sources were retrieved.

IMPORTANT:
- Do not create fake statistics.
- Do not mention fake surveys.
- Do not invent companies or studies.
- Give cautious market analysis only.

"""


    lines = [

        "LIVE RETRIEVED SOURCES:",

        "",

        "Use these sources as evidence.",

        "Do not invent facts that are not supported.",

        "",

    ]


    for index, source in enumerate(
        sources,
        start=1
    ):

        lines.append(

            f"""
SOURCE {index}

Title:
{source.title}

URL:
{source.url}

Evidence:
{source.snippet}

"""
        )


    return "\n".join(lines)