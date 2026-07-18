"""
FYPilot LLM Provider Layer

INF-1 / RTA-1 mini implementation.

Provider order:
1. GroqProvider
   - Normal mode: llama-3.3-70b-versatile
   - Search mode: groq/compound-mini

2. GeminiProvider
   - Uses existing GeminiClient
   - Can use Google Search grounding if available

3. OllamaProvider
   - Local fallback using qwen2.5-coder:7b by default

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


class BaseProvider:
    name: str = "base"

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
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

        client = Groq(api_key=self.api_key)

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

            client = Groq(api_key=self.api_key)

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
                max_tokens=2200,
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
                    "format": "json",
                    "options": {
                        "temperature": 0.2,
                        "num_ctx": 4096,
                    },
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


class ProviderChain:
    """
    Provider cascade.

    Default order:
    1. Groq
    2. Gemini
    3. Ollama

    This makes Groq the main provider, Gemini the backup cloud provider,
    and Ollama the local fallback.
    """

    def __init__(self, providers: list[BaseProvider] | None = None):
        self.providers = providers or [
            GroqProvider(),
            GeminiProvider(),
            OllamaProvider(),
        ]

    def search_web(self, query: str) -> LLMResult:
        """
        Run direct web search with the first provider that implements it.

        In the default chain this is Groq Compound Mini. Generation providers
        are not silently substituted for the search provider because the API
        must report whether real source URLs were actually obtained.
        """
        errors: list[str] = []

        for provider in self.providers:
            if provider.__class__.search_web is BaseProvider.search_web:
                continue

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
    ) -> LLMResult:
        errors: list[str] = []

        for provider in self.providers:
            result = provider.generate_json(
                prompt,
                use_search=use_search,
            )

            if result.ok and result.data is not None:
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