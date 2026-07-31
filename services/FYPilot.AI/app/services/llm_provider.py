"""
FYPilot LLM Provider Layer

INF-1 / RTA-1 mini implementation.

There are TWO completely separate provider chains. They never substitute
for each other -- a search provider never generates the final agent
answer, and a generation provider never performs live web search.

GENERATION chain (ProviderChain.providers, used by generate_json/generate_text):
1. DeepInfraProvider
   - Paid, pay-per-use OpenAI-compatible endpoint (no free-tier rate limiting)
   - Default model: meta-llama/Llama-3.3-70B-Instruct

2. GroqProvider
   - Normal mode: llama-3.3-70b-versatile (this is the generation fallback --
     NOT groq/compound-mini, which is search-only, see below)

3. OllamaProvider
   - Local fallback using qwen2.5-coder:7b by default

WEB-SEARCH chain (ProviderChain.search_providers, used only by search_web()):
1. BraveSearchProvider
   - Brave's LLM Context API -- primary web-search provider

2. GroqProvider (search_web mode only)
   - Search mode: groq/compound-mini -- secondary/fallback web search only,
     never used for normal generation

3. (no further fallback -- callers proceed with no live evidence)

GeminiProvider still exists below (unused in the default chain) in case it
needs to be re-added later -- see git history for when/why it was dropped.

This file lets agents switch providers without rewriting every agent.
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests


@dataclass
class LLMResult:
    ok: bool
    provider: str
    model: str | None
    text: str
    data: dict[str, Any] | None
    error: str | None = None
    search_used: bool = False
    search_failed: bool = False

    # Sources are extracted only from provider tool metadata, never invented
    # from model-generated JSON.
    sources: list[dict[str, str]] = field(default_factory=list)
    executed_tools: list[dict[str, Any]] = field(default_factory=list)


def _clean_url(value: str) -> str:
    """Return a clean http/https URL or an empty string."""
    url = str(value or "").strip().strip('"\'<>')
    url = url.rstrip('.,;:!?)]}')

    if not url.lower().startswith(("http://", "https://")):
        return ""

    return url


def _fallback_title_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host.removeprefix("www.") or "Web source"
    except Exception:
        return "Web source"


def _normalize_tool_data(value: Any) -> Any:
    """Convert SDK objects into normal Python dictionaries/lists."""
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool, dict, list)):
        return value

    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass

    if hasattr(value, "dict"):
        try:
            return value.dict()
        except Exception:
            pass

    return str(value)


def _source_from_mapping(value: dict[str, Any]) -> dict[str, str] | None:
    url = _clean_url(
        value.get("url")
        or value.get("link")
        or value.get("href")
        or value.get("source_url")
        or value.get("sourceUrl")
        or ""
    )

    if not url:
        return None

    title = str(
        value.get("title")
        or value.get("name")
        or value.get("source")
        or _fallback_title_from_url(url)
    ).strip()

    snippet = str(
        value.get("snippet")
        or value.get("description")
        or value.get("content")
        or value.get("text")
        or ""
    ).strip()

    return {
        "title": title[:240],
        "url": url,
        "snippet": snippet[:500],
    }


def _extract_sources_from_text(text: str) -> list[dict[str, str]]:
    """Extract URLs and nearby titles from actual tool-output text."""
    if not text:
        return []

    found: list[dict[str, str]] = []

    # Structured JSON tool output is common, so inspect it first.
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
            found.extend(_extract_sources_from_value(parsed))
        except Exception:
            pass

    # Markdown links: [Article title](https://example.com/page)
    markdown_pattern = re.compile(r"\[([^\]]{1,240})\]\((https?://[^)\s]+)\)")
    for match in markdown_pattern.finditer(text):
        url = _clean_url(match.group(2))
        if url:
            found.append({
                "title": match.group(1).strip(),
                "url": url,
                "snippet": "",
            })

    # Plain URLs. Use the closest preceding Title: line when present.
    url_pattern = re.compile(r"https?://[^\s<>\"']+")
    for match in url_pattern.finditer(text):
        url = _clean_url(match.group(0))
        if not url:
            continue

        prefix = text[max(0, match.start() - 500):match.start()]
        title_matches = re.findall(
            r"(?:^|\n)\s*(?:title|source|name)\s*:\s*([^\n|]{1,240})",
            prefix,
            flags=re.IGNORECASE,
        )
        title = (
            title_matches[-1].strip()
            if title_matches
            else _fallback_title_from_url(url)
        )

        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end == -1:
            line_end = len(text)
        snippet = text[line_start:line_end].strip()

        found.append({
            "title": title[:240],
            "url": url,
            "snippet": snippet[:500],
        })

    return found


def _extract_sources_from_value(value: Any) -> list[dict[str, str]]:
    value = _normalize_tool_data(value)
    found: list[dict[str, str]] = []

    if isinstance(value, dict):
        direct = _source_from_mapping(value)
        if direct:
            found.append(direct)

        for nested in value.values():
            found.extend(_extract_sources_from_value(nested))

    elif isinstance(value, list):
        for item in value:
            found.extend(_extract_sources_from_value(item))

    elif isinstance(value, str):
        found.extend(_extract_sources_from_text(value))

    return found


def _deduplicate_sources(
    sources: list[dict[str, str]],
    *,
    limit: int = 10,
) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen: set[str] = set()

    for source in sources:
        url = _clean_url(source.get("url", ""))
        if not url or url in seen:
            continue

        seen.add(url)
        unique.append({
            "title": str(source.get("title") or _fallback_title_from_url(url))[:240],
            "url": url,
            "snippet": str(source.get("snippet") or "")[:500],
        })

        if len(unique) >= limit:
            break

    return unique


def _normalize_executed_tools(value: Any) -> list[dict[str, Any]]:
    normalized = _normalize_tool_data(value)

    if not isinstance(normalized, list):
        return []

    tools: list[dict[str, Any]] = []
    for item in normalized:
        item = _normalize_tool_data(item)
        if isinstance(item, dict):
            tools.append(item)

    return tools


def _extract_sources_from_executed_tools(
    executed_tools: list[dict[str, Any]],
) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []

    for tool in executed_tools:
        # Search results are returned inside the actual executed tool output.
        found.extend(_extract_sources_from_value(tool.get("output")))

        # Some SDK/API versions expose results under other keys.
        found.extend(_extract_sources_from_value(tool.get("results")))
        found.extend(_extract_sources_from_value(tool.get("search_results")))
        found.extend(_extract_sources_from_value(tool.get("citations")))

    return _deduplicate_sources(found)


def _used_web_tool(executed_tools: list[dict[str, Any]]) -> bool:
    for tool in executed_tools:
        tool_type = str(
            tool.get("type")
            or tool.get("name")
            or tool.get("tool_name")
            or ""
        ).lower()

        if "search" in tool_type or "visit" in tool_type or "browser" in tool_type:
            return True

    return False


def _basic_secret_scan_ok(result: "LLMResult") -> bool:
    """
    Universal, context-free backstop applied to EVERY provider response,
    regardless of which agent called ProviderChain -- including the agents
    not (yet) wired into the full review pipeline. Only checks for hard,
    high-confidence secret patterns via app.llm_firewall.rules.secrets; the
    rich, context-aware LlmFirewall used by ReviewPipeline is a separate,
    more thorough check that only protects agents actually wired into it
    (see app/review/pipeline.py and the honest-scope note there).

    Returns False only when a "block"-severity secret pattern is found, in
    which case ProviderChain treats this provider's response as unusable and
    falls through to the next provider in the cascade -- the same behavior
    as any other provider failure.
    """
    from app.llm_firewall.rules import secrets as secret_rules

    text_to_scan = result.text or ""

    if result.data is not None:
        try:
            text_to_scan += json.dumps(result.data, ensure_ascii=False, default=str)
        except Exception:
            text_to_scan += str(result.data)

    if not text_to_scan:
        return True

    findings = secret_rules.scan({"provider_response": text_to_scan})
    return not any(finding.action == "block" for finding in findings)


class BaseProvider:
    name: str = "base"

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
    ) -> LLMResult:
        raise NotImplementedError

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
    ) -> LLMResult:
        raise NotImplementedError

    def search_web(self, query: str) -> LLMResult:
        return LLMResult(
            ok=False,
            provider=self.name,
            model=None,
            text="",
            data=None,
            error=f"{self.name} does not implement direct web search",
            search_used=False,
            search_failed=True,
        )


class DeepInfraProvider(BaseProvider):
    """
    Primary cloud provider.

    Paid, pay-per-use inference with no free-tier rate limiting -- unlike
    Groq's free tier, which the on-demand/dev-tier gating made unreliable
    during time-sensitive use (e.g. a live presentation).

    OpenAI-compatible endpoint (https://api.deepinfra.com/v1/openai), so it
    reuses the `openai` SDK already in requirements.txt instead of a new
    dependency. Does not implement search_web -- DeepInfra has no built-in
    web-search tool equivalent to Groq Compound.

    Model defaults to DEEPINFRA_MODEL (or the tier-specific env var resolved
    by _deepinfra_model_for_tier), but callers needing a specific model for
    their task's accuracy/cost tradeoff can pass one explicitly -- see
    ProviderChain(tier=...).
    """

    name = "deepinfra"

    def __init__(self, model: str | None = None):
        self.api_key = os.getenv("DEEPINFRA_API_KEY")

        self.model = model or os.getenv(
            "DEEPINFRA_MODEL",
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        )

        # Mirrors GroqProvider's SEC-3 per-call timeout rationale: a hung
        # request must not block a review-pipeline attempt indefinitely.
        self.timeout_seconds = float(os.getenv("DEEPINFRA_TIMEOUT_SECONDS", "60"))

        self.enabled = bool(self.api_key)

    def _client(self):
        from openai import OpenAI

        return OpenAI(
            api_key=self.api_key,
            base_url="https://api.deepinfra.com/v1/openai",
            timeout=self.timeout_seconds,
        )

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
    ) -> LLMResult:
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="DEEPINFRA_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
            )

        try:
            system_message = (
                "You are a precise JSON-only AI engine. "
                "Return valid JSON only. "
                "Do not use markdown. "
                "Do not wrap the response in code fences."
            )

            response = self._client().chat.completions.create(
                model=self.model,
                temperature=0.2,
                # Same rationale as GroqProvider: SE Documentation's richer
                # sections pass an explicit higher budget so responses
                # aren't silently truncated into invalid JSON.
                max_tokens=max_tokens or 2200,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                ],
            )

            text = str(response.choices[0].message.content or "")
            data = _parse_json(text)

            return LLMResult(
                ok=data is not None,
                provider=self.name,
                model=self.model,
                text=text,
                data=data,
                error=None if data is not None else "DeepInfra returned invalid JSON.",
                search_used=False,
                search_failed=use_search,
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
            )

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
    ) -> LLMResult:
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="DEEPINFRA_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
            )

        try:
            response = self._client().chat.completions.create(
                model=self.model,
                temperature=0.3,
                max_tokens=1800,
                messages=[
                    {"role": "system", "content": "You are a helpful AI assistant."},
                    {"role": "user", "content": prompt},
                ],
            )

            text = str(response.choices[0].message.content or "")

            return LLMResult(
                ok=True,
                provider=self.name,
                model=self.model,
                text=text,
                data=None,
                error=None,
                search_used=False,
                search_failed=use_search,
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
            )


class BraveSearchProvider(BaseProvider):
    """
    Primary WEB-SEARCH provider -- NOT a generation provider.

    This class deliberately does not override generate_json/generate_text
    (they raise NotImplementedError via BaseProvider), and this instance is
    NEVER placed in ProviderChain.providers (the generation chain) -- only
    in ProviderChain.search_providers. This keeps web search and answer
    generation as two genuinely separate chains, per design: Brave must
    never generate the final agent answer.

    Uses Brave's LLM Context API (POST /res/v1/llm/context), which returns
    pre-extracted snippets directly -- no separate page-fetch step is
    needed or performed.
    """

    name = "brave"

    def __init__(self):
        self.api_key = os.getenv("BRAVE_SEARCH_API_KEY")

        self.enabled = (
            os.getenv("BRAVE_SEARCH_ENABLED", "true").strip().lower() == "true"
            and bool(self.api_key)
        )

        self.timeout_seconds = float(os.getenv("BRAVE_SEARCH_TIMEOUT_SECONDS", "30"))
        self.max_urls = int(os.getenv("BRAVE_SEARCH_MAX_URLS", "8"))
        self.max_tokens = int(os.getenv("BRAVE_SEARCH_MAX_TOKENS", "4096"))
        self.country = os.getenv("BRAVE_SEARCH_COUNTRY", "").strip()
        self.language = os.getenv("BRAVE_SEARCH_LANGUAGE", "en").strip()

        self.endpoint = "https://api.search.brave.com/res/v1/llm/context"
        self.model_name = "brave-llm-context"

    # Confirmed empirically against the live Brave LLM Context API: queries
    # of 360 chars succeeded, 373 chars returned HTTP 422 (Unprocessable
    # Entity) -- the real boundary sits between the two. 320 is a safe
    # margin below it. Existing agent queries (e.g. FypMentorAgent's
    # instructional wrapper around the student's question, up to ~373
    # chars) previously exceeded Brave's real limit although comfortably
    # under this class's old 400-char cap -- this is the fix for that.
    _MAX_QUERY_LENGTH = 320

    @staticmethod
    def _clean_query(query: str) -> str:
        """
        Send only the intended search query to Brave -- never the full agent
        prompt. Strips control characters and collapses whitespace without
        altering the query's meaning; truncated to _MAX_QUERY_LENGTH to
        respect Brave's real query-length restriction (see comment above).
        """
        text = query or ""
        cleaned = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch >= " ")
        cleaned = " ".join(cleaned.split()).strip()
        return cleaned[:BraveSearchProvider._MAX_QUERY_LENGTH]

    def search_web(self, query: str) -> LLMResult:
        if not self.enabled:
            reason = (
                "BRAVE_SEARCH_API_KEY is missing"
                if not self.api_key
                else "Brave search is disabled by configuration"
            )
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model_name,
                text="",
                data=None,
                error=reason,
                search_used=False,
                search_failed=True,
            )

        clean_query = self._clean_query(query)

        if not clean_query:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model_name,
                text="",
                data=None,
                error="Empty search query after sanitization",
                search_used=False,
                search_failed=True,
            )

        request_body: dict[str, Any] = {
            "q": clean_query,
            "count": 10,
            "maximum_number_of_urls": self.max_urls,
            "maximum_number_of_tokens": self.max_tokens,
            "maximum_number_of_snippets": 24,
            "maximum_number_of_tokens_per_url": 1024,
            "maximum_number_of_snippets_per_url": 3,
            "context_threshold_mode": "balanced",
        }

        # Do not send empty optional fields.
        if self.country:
            request_body["country"] = self.country
        if self.language:
            request_body["language"] = self.language

        try:
            # Exactly one request per search_web() call -- no retries, no
            # parallel Brave+Groq calls. On any failure below, ProviderChain
            # (not this class) moves on to Groq.
            response = requests.post(
                self.endpoint,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    # Never logged -- see _request_error(), which only ever
                    # records the exception TYPE, never headers or the key.
                    "X-Subscription-Token": self.api_key,
                },
                json=request_body,
                timeout=self.timeout_seconds,
            )
        except requests.exceptions.Timeout:
            return self._search_error("Brave search request timed out")
        except requests.exceptions.RequestException as ex:
            return self._search_error(f"Brave search connection error: {type(ex).__name__}")

        if response.status_code != 200:
            # Covers 400, 401/403, 429, and 5xx alike -- all are simply
            # "Brave unsuccessful", never distinguished further to callers,
            # and never expose response body content (which could echo the
            # request or account details).
            return self._search_error(f"Brave search returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError:
            return self._search_error("Brave search returned invalid JSON")

        sources = self._parse_response(payload)

        if not sources:
            return self._search_error("Brave search returned no usable safe sources")

        return LLMResult(
            ok=True,
            provider=self.name,
            model=self.model_name,
            text="",
            data=None,
            error=None,
            search_used=True,
            search_failed=False,
            sources=sources,
        )

    def _search_error(self, message: str) -> LLMResult:
        return LLMResult(
            ok=False,
            provider=self.name,
            model=self.model_name,
            text="",
            data=None,
            error=message,
            search_used=False,
            search_failed=True,
        )

    def _parse_response(self, payload: Any) -> list[dict[str, str]]:
        """
        Parses the documented Brave LLM Context response shape:
        grounding.generic[] (url, title, snippets[]) plus an optional
        sources{} map keyed by URL for extra metadata.

        Maps into the project's EXISTING SearchSource contract (see
        _source_from_mapping/_deduplicate_sources above: {title, url,
        snippet}) rather than a new, incompatible model -- every downstream
        consumer (agents' _format_sources_for_prompt, the .NET
        IdeaEvidenceSourceDto mapping) already expects exactly these three
        keys. Hostname/date fields Brave may provide are deliberately not
        added to this shared dict: nothing downstream reads them today, and
        inventing new keys here would make Brave sources incompatible with
        every existing consumer of Groq-produced sources.
        """
        if not isinstance(payload, dict):
            return []

        grounding = payload.get("grounding")
        if not isinstance(grounding, dict):
            return []

        generic = grounding.get("generic")
        if not isinstance(generic, list):
            return []

        source_metadata = payload.get("sources")
        metadata_by_url: dict[str, dict[str, Any]] = {}
        if isinstance(source_metadata, dict):
            for key, value in source_metadata.items():
                if isinstance(value, dict):
                    metadata_by_url[_clean_url(str(key))] = value

        found: list[dict[str, str]] = []

        for item in generic:
            if not isinstance(item, dict):
                continue

            url = _clean_url(str(item.get("url") or ""))
            if not url:
                continue

            title = str(item.get("title") or "").strip()

            snippets = item.get("snippets")
            snippet_text = ""
            if isinstance(snippets, list):
                snippet_text = " ".join(
                    str(snippet).strip()
                    for snippet in snippets[:3]
                    if str(snippet).strip()
                )[:500]

            if not title:
                meta = metadata_by_url.get(url, {})
                title = str(meta.get("title") or "").strip()

            found.append({
                "title": (title or _fallback_title_from_url(url))[:240],
                "url": url,
                "snippet": snippet_text,
            })

        return _deduplicate_sources(found, limit=self.max_urls)


class GroqProvider(BaseProvider):
    """
    Primary cloud provider.

    use_search=False:
        Uses GROQ_MODEL, default llama-3.3-70b-versatile.

    use_search=True:
        Uses GROQ_SEARCH_MODEL, default groq/compound-mini.

    Groq Compound returns its server-side tool calls in message.executed_tools.
    This provider reads those raw tool results and extracts only real URLs that
    were returned by the web-search tool.
    """

    name = "groq"

    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")

        self.model = os.getenv(
            "GROQ_MODEL",
            "llama-3.3-70b-versatile",
        )

        self.search_model = os.getenv(
            "GROQ_SEARCH_MODEL",
            "groq/compound-mini",
        )

        # SEC-3: an explicit per-call timeout is required so a hung Groq
        # request cannot block a review-pipeline attempt indefinitely --
        # this is a prerequisite for (and separate from) the pipeline's own
        # aggregate wall-clock budget, which only checks time BETWEEN
        # iterations and cannot interrupt a single in-flight call.
        self.timeout_seconds = float(os.getenv("GROQ_TIMEOUT_SECONDS", "60"))

        self.enabled = bool(self.api_key)
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def _request(
        self,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        messages: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
        """Normal Groq chat request used for structured generation."""
        from groq import Groq

        client = Groq(api_key=self.api_key, timeout=self.timeout_seconds)

        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=messages,
        )

        message = response.choices[0].message
        text = str(message.content or "")
        executed_tools = _normalize_executed_tools(
            getattr(message, "executed_tools", None) or []
        )
        sources = _extract_sources_from_executed_tools(executed_tools)

        return text, executed_tools, sources

    def search_web(self, query: str) -> LLMResult:
        """
        Run a small dedicated Groq Compound Mini web-search request.

        Search is separated from the large structured idea-generation prompt.
        This keeps the Compound request small and prevents 413 errors caused by
        combining web search, a long schema, and four complete idea objects.
        """
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="GROQ_API_KEY is missing",
                search_used=False,
                search_failed=True,
            )

        try:
            from groq import Groq

            client = Groq(api_key=self.api_key, timeout=self.timeout_seconds)

            response = client.chat.completions.create(
                model=self.search_model,
                messages=[
                    {
                        "role": "user",
                        "content": query,
                    }
                ],
            )

            message = response.choices[0].message
            text = str(message.content or "")
            executed_tools = _normalize_executed_tools(
                getattr(message, "executed_tools", None) or []
            )
            sources = _extract_sources_from_executed_tools(executed_tools)
            web_tool_used = _used_web_tool(executed_tools)

            return LLMResult(
                ok=bool(web_tool_used and sources),
                provider=self.name,
                model=self.search_model,
                text=text,
                data=None,
                error=(
                    None
                    if web_tool_used and sources
                    else "Groq Compound returned no usable web-search sources."
                ),
                search_used=web_tool_used,
                search_failed=not web_tool_used or not sources,
                sources=sources,
                executed_tools=executed_tools,
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.search_model,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=True,
            )

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
    ) -> LLMResult:
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="GROQ_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
            )

        model_to_use = self.search_model if use_search else self.model

        try:
            system_message = (
                "You are a precise JSON-only AI engine. "
                "Return valid JSON only. "
                "Do not use markdown. "
                "Do not wrap the response in code fences."
            )

            if use_search:
                system_message += (
                    " You must use the live web-search tool before answering. "
                    "Use current evidence for market, trend, competitor, and adoption claims. "
                    "Do not invent citations or URLs. "
                    "The application will read real sources directly from tool metadata, "
                    "so do not place a sources list inside the JSON."
                )

            text, executed_tools, sources = self._request(
                model=model_to_use,
                temperature=0.2,
                # Default (2200) fits every other agent's existing JSON shape.
                # SE Documentation's richer, higher-count sections (12-20
                # functional requirements, 10-18 test cases, ...) pass an
                # explicit higher budget -- without it, those responses were
                # silently truncated into invalid JSON, which looked like a
                # provider failure and collapsed the whole section to a
                # generic deterministic fallback.
                max_tokens=max_tokens or 2200,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                ],
            )

            data = _parse_json(text)
            web_tool_used = _used_web_tool(executed_tools)

            return LLMResult(
                ok=data is not None,
                provider=self.name,
                model=model_to_use,
                text=text,
                data=data,
                error=None if data is not None else "Groq returned invalid JSON.",
                search_used=web_tool_used,
                search_failed=bool(use_search and not web_tool_used),
                sources=sources,
                executed_tools=executed_tools,
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=model_to_use,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
            )

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
    ) -> LLMResult:
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="GROQ_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
            )

        model_to_use = self.search_model if use_search else self.model

        try:
            system_message = "You are a helpful AI assistant."
            if use_search:
                system_message += (
                    " Use live web search before answering current factual questions. "
                    "Do not invent citations or URLs."
                )

            text, executed_tools, sources = self._request(
                model=model_to_use,
                temperature=0.3,
                max_tokens=1800,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                ],
            )

            web_tool_used = _used_web_tool(executed_tools)

            return LLMResult(
                ok=True,
                provider=self.name,
                model=model_to_use,
                text=text,
                data=None,
                error=None,
                search_used=web_tool_used,
                search_failed=bool(use_search and not web_tool_used),
                sources=sources,
                executed_tools=executed_tools,
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=model_to_use,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
            )


class GeminiProvider(BaseProvider):
    """
    Secondary cloud provider.

    Uses your existing app/services/gemini_client.py.
    """

    name = "gemini"

    def __init__(self):
        self.enabled = bool(os.getenv("GEMINI_API_KEY"))

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
    ) -> LLMResult:
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="GEMINI_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
            )

        try:
            from app.services.gemini_client import GeminiClient

            client = GeminiClient()

            # Gemini's SDK has no explicit output-token cap in GeminiClient
            # today, so max_tokens is accepted (to keep this signature
            # interchangeable with GroqProvider/OllamaProvider) but unused.
            data = client.generate_json(
                prompt,
                use_search=use_search,
                fallback_without_search=True,
            )

            return LLMResult(
                ok=True,
                provider=self.name,
                model=getattr(client, "model_used", None)
                or os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
                text="",
                data=data,
                error=getattr(client, "last_error", None),
                search_used=getattr(client, "search_used", False),
                search_failed=getattr(client, "search_failed", False),
                sources=_deduplicate_sources(
                    list(getattr(client, "sources", []) or [])
                    + list(getattr(client, "grounding_sources", []) or [])
                ),
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
            )

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
    ) -> LLMResult:
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="GEMINI_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
            )

        try:
            from app.services.gemini_client import GeminiClient

            client = GeminiClient()

            text = client.generate_text(
                prompt,
                use_search=use_search,
                fallback_without_search=True,
            )

            return LLMResult(
                ok=True,
                provider=self.name,
                model=getattr(client, "model_used", None)
                or os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
                text=text,
                data=None,
                error=getattr(client, "last_error", None),
                search_used=getattr(client, "search_used", False),
                search_failed=getattr(client, "search_failed", False),
                sources=_deduplicate_sources(
                    list(getattr(client, "sources", []) or [])
                    + list(getattr(client, "grounding_sources", []) or [])
                ),
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
            )


class OllamaProvider(BaseProvider):
    """
    Local fallback provider.

    Uses Ollama /api/generate.
    """

    name = "ollama"

    def __init__(self):
        self.base_url = os.getenv(
            "OLLAMA_BASE_URL",
            "http://localhost:11434",
        ).rstrip("/")

        self.model = os.getenv(
            "OLLAMA_MODEL",
            "qwen2.5-coder:7b",
        )

        self.enabled = (
            os.getenv("OLLAMA_FALLBACK_ENABLED", "true").lower()
            == "true"
        )

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
    ) -> LLMResult:
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error="OLLAMA_FALLBACK_ENABLED is false",
                search_used=False,
                search_failed=False,
            )

        try:
            options: dict[str, Any] = {
                "temperature": 0.2,
                # Scale the context window with the requested output budget
                # so a larger SE Documentation section isn't cut short by a
                # fixed 4096-token window -- default preserved for every
                # other caller that doesn't pass max_tokens.
                "num_ctx": max(4096, max_tokens * 2) if max_tokens else 4096,
            }
            if max_tokens:
                options["num_predict"] = max_tokens

            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": options,
                },
                timeout=(5, 90),
            )

            response.raise_for_status()

            payload = response.json()
            text = payload.get("response", "")
            data = _parse_json(text)

            return LLMResult(
                ok=True,
                provider=self.name,
                model=self.model,
                text=text,
                data=data,
                error=None,
                search_used=False,
                search_failed=False,
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=False,
            )

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
    ) -> LLMResult:
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error="OLLAMA_FALLBACK_ENABLED is false",
                search_used=False,
                search_failed=False,
            )

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_ctx": 4096,
                    },
                },
                timeout=(5, 90),
            )

            response.raise_for_status()

            payload = response.json()
            text = payload.get("response", "")

            return LLMResult(
                ok=True,
                provider=self.name,
                model=self.model,
                text=text,
                data=None,
                error=None,
                search_used=False,
                search_failed=False,
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=False,
            )


# Per-task accuracy/cost tier for the DeepInfra leg of the chain -- some
# agents (SE Documentation) need the highest-accuracy model available,
# others (Defense Simulator's question/evaluation prompts) don't, so a
# single global DEEPINFRA_MODEL would either overpay everywhere or
# underpower the agents that need it most. Each tier's model is still
# overridable per-deployment via its own env var.
_DEEPINFRA_TIER_DEFAULTS: dict[str, str] = {
    # Highest-accuracy tier: strict-schema, high-stakes generation (SE Docs,
    # Project Roadmap, Idea Generator).
    # NOTE: DeepInfra's API requires the full "org/model" slug -- the bare
    # display name from DeepInfra's own pricing table (e.g. "claude-opus-4-8")
    # 404s as model_not_found. Verified live against the real API.
    "high": "anthropic/claude-opus-4-8",
    # Default tier: most agents (market needs, project DNA, market
    # footprint, idea comparison) -- confirmed working, cheap, good
    # instruction-following.
    "standard": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    # Lightweight tier: short, simple prompts (Defense Simulator).
    "light": "google/gemma-3-12b-it",
    # Mentor Chat tier: interactive, latency-sensitive, needs strong coding
    # ability (generates codeBlocks) and broad multilingual support -- Qwen3
    # Coder is a coding-specialized MoE model (fast for its size due to
    # sparse activation) rather than a general dense model tuned for one
    # of those three at the expense of the others.
    "mentor": "Qwen/Qwen3-Coder-480B-A35B-Instruct-Turbo",
}


def _deepinfra_model_for_tier(tier: str) -> str:
    resolved_tier = tier if tier in _DEEPINFRA_TIER_DEFAULTS else "standard"
    env_key = f"DEEPINFRA_MODEL_{resolved_tier.upper()}"
    return os.getenv(env_key, _DEEPINFRA_TIER_DEFAULTS[resolved_tier])


class ProviderChain:
    """
    Two genuinely separate provider cascades, both owned by this class.

    GENERATION chain (self.providers, used by generate_json/generate_text):
    1. DeepInfra
    2. Groq
    3. Ollama

    This makes DeepInfra (paid, no free-tier rate limiting) the main
    provider, Groq the backup cloud provider, and local Ollama the final
    fallback. GeminiProvider is intentionally excluded from the default
    chain -- pass providers explicitly to include it.

    `tier` selects the DeepInfra model ("high" / "standard" / "light" /
    "mentor", see _DEEPINFRA_TIER_DEFAULTS) and is ignored if `providers`
    is passed explicitly. Brave is never part of this chain -- it has no
    generate_json/generate_text implementation and must never generate an
    agent's final answer.

    WEB-SEARCH chain (self.search_providers, used only by search_web()):
    1. Brave (BraveSearchProvider) -- primary
    2. Groq (search_web mode, groq/compound-mini) -- fallback
    (no further fallback -- callers proceed with no live evidence)

    Kept as a genuinely separate list from `self.providers` so a change to
    one chain (e.g. swapping the primary search provider) can never affect
    the other (e.g. which model answers a student's question).
    """

    def __init__(
        self,
        providers: list[BaseProvider] | None = None,
        tier: str = "standard",
        search_providers: list[BaseProvider] | None = None,
    ):
        self.providers = providers or [
            DeepInfraProvider(model=_deepinfra_model_for_tier(tier)),
            GroqProvider(),
            OllamaProvider(),
        ]

        self.search_providers = search_providers or [
            BraveSearchProvider(),
            GroqProvider(),
        ]

    def search_web(self, query: str) -> LLMResult:
        """
        Run direct web search with the first search_providers entry that
        succeeds: Brave first, Groq Compound second, matching the class
        docstring's WEB-SEARCH chain. Generation providers are never
        substituted for the search chain (this loop no longer touches
        self.providers at all) because the API must report whether real
        source URLs were actually obtained, and because Brave/Groq search
        must stay fully independent of which generation provider is active.
        """
        errors: list[str] = []

        for provider in self.search_providers:
            result = provider.search_web(query)

            if result.ok and result.search_used and result.sources:
                return result

            errors.append(
                f"{result.provider}:{result.model} -> {result.error}"
            )

        return LLMResult(
            ok=False,
            provider="none",
            model=None,
            text="",
            data=None,
            error="Web search failed. " + " | ".join(errors),
            search_used=False,
            search_failed=True,
        )

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
    ) -> LLMResult:
        errors: list[str] = []

        for provider in self.providers:
            result = provider.generate_json(
                prompt,
                use_search=use_search,
                max_tokens=max_tokens,
            )

            if result.ok and result.data is not None:
                if not _basic_secret_scan_ok(result):
                    errors.append(
                        f"{result.provider}:{result.model} -> response withheld by basic secret scan"
                    )
                    continue

                return result

            errors.append(
                f"{result.provider}:{result.model} -> {result.error}"
            )

        return LLMResult(
            ok=False,
            provider="none",
            model=None,
            text="",
            data=None,
            error="All providers failed. " + " | ".join(errors),
            search_used=False,
            search_failed=use_search,
        )

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
    ) -> LLMResult:
        errors: list[str] = []

        for provider in self.providers:
            result = provider.generate_text(
                prompt,
                use_search=use_search,
            )

            if result.ok and result.text:
                if not _basic_secret_scan_ok(result):
                    errors.append(
                        f"{result.provider}:{result.model} -> response withheld by basic secret scan"
                    )
                    continue

                return result

            errors.append(
                f"{result.provider}:{result.model} -> {result.error}"
            )

        return LLMResult(
            ok=False,
            provider="none",
            model=None,
            text="",
            data=None,
            error="All providers failed. " + " | ".join(errors),
            search_used=False,
            search_failed=use_search,
        )


def _parse_json(text: str) -> dict[str, Any]:
    """
    Parse strict JSON, or extract the first JSON object from messy model output.
    """

    cleaned = (text or "").strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.replace("```json", "")
        cleaned = cleaned.replace("```", "")
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise ValueError(
            f"No valid JSON found in model output. Preview: {cleaned[:500]}"
        )

    return json.loads(cleaned[start:end + 1])