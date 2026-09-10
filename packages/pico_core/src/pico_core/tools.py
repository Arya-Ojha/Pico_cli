"""The core tools: read, write, edit, bash, grep, fetch, websearch.

Each tool operates against a working directory. Bash runs unsandboxed and is
disabled unless an opt-in flag is set. Tool errors (missing file, non-zero exit)
surface as results rather than exceptions.
"""

from __future__ import annotations

import asyncio
import fnmatch
import html as _html
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from pico_ai.types import ToolDefinition


class ToolOutcome(BaseModel):
    """The result of running a tool."""

    content: str
    is_error: bool = False


class Tool(Protocol):
    """A capability the agent can invoke."""

    name: str
    description: str
    input_schema: dict

    async def run(self, arguments: dict) -> ToolOutcome:
        """Execute the tool and return its outcome."""
        ...


def _resolve(cwd: Path, raw: str) -> Path:
    """Resolve a possibly-relative path against the working directory."""
    p = Path(raw)
    return p if p.is_absolute() else cwd / p


class ReadTool:
    """Read a file's contents."""

    name = "read"
    description = "Read the contents of a file."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    async def run(self, arguments: dict) -> ToolOutcome:
        path = _resolve(self._cwd, arguments["path"])
        try:
            return ToolOutcome(content=path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ToolOutcome(
                content=f"error: file not found: {arguments['path']}", is_error=True
            )
        except OSError as exc:
            return ToolOutcome(content=f"error: {exc}", is_error=True)


class WriteTool:
    """Create or overwrite a file."""

    name = "write"
    description = "Create or overwrite a file with the given content."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    async def run(self, arguments: dict) -> ToolOutcome:
        path = _resolve(self._cwd, arguments["path"])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(arguments.get("content", ""), encoding="utf-8")
            return ToolOutcome(content=f"wrote {arguments['path']}")
        except OSError as exc:
            return ToolOutcome(content=f"error: {exc}", is_error=True)


class EditTool:
    """Apply a surgical search/replace patch to a file."""

    name = "edit"
    description = "Replace a unique occurrence of old_text with new_text in a file."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
    }

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    async def run(self, arguments: dict) -> ToolOutcome:
        path = _resolve(self._cwd, arguments["path"])
        old = arguments["old_text"]
        new = arguments.get("new_text", "")
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ToolOutcome(
                content=f"error: file not found: {arguments['path']}", is_error=True
            )
        count = content.count(old)
        if count == 0:
            return ToolOutcome(content="error: old_text not found", is_error=True)
        if count > 1:
            return ToolOutcome(
                content=f"error: old_text found {count} times; provide a unique match",
                is_error=True,
            )
        try:
            path.write_text(content.replace(old, new, 1), encoding="utf-8")
            return ToolOutcome(content=f"edited {arguments['path']}")
        except OSError as exc:
            return ToolOutcome(content=f"error: {exc}", is_error=True)


class BashTool:
    """Run a shell command (unsandboxed, opt-in)."""

    name = "bash"
    description = "Run a shell command and return its output and exit code."
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }

    def __init__(self, cwd: Path, enabled: bool = False) -> None:
        self._cwd = cwd
        self.enabled = enabled

    async def run(self, arguments: dict) -> ToolOutcome:
        if not self.enabled:
            return ToolOutcome(
                content="error: bash is disabled; drop --no-bash to enable",
                is_error=True,
            )
        command = arguments["command"]
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(self._cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
            content = output.rstrip() + f"\n[exit code: {proc.returncode}]"
            return ToolOutcome(content=content, is_error=proc.returncode != 0)
        except Exception as exc:  # noqa: BLE001 - surface any failure as a result
            return ToolOutcome(content=f"error: {exc}", is_error=True)


class GrepTool:
    """Search file contents for a regex pattern."""

    name = "grep"
    description = (
        "Search file contents for a regex pattern. Searches a single file or "
        "recursively under a directory; returns matching lines as "
        "`path:line: content`."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "include": {"type": "string"},
        },
        "required": ["pattern"],
    }

    # Directories never descended into during a recursive search.
    _SKIP_DIRS = frozenset(
        {
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            "node_modules",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "dist",
            "build",
        }
    )
    _MAX_MATCHES = 100
    _MAX_LINE_LEN = 200

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    async def run(self, arguments: dict) -> ToolOutcome:
        raw_pattern = arguments.get("pattern", "")
        if not raw_pattern:
            return ToolOutcome(content="error: pattern is required", is_error=True)
        try:
            regex = re.compile(raw_pattern)
        except re.error as exc:
            return ToolOutcome(content=f"error: invalid regex: {exc}", is_error=True)

        target = _resolve(self._cwd, arguments.get("path", ".") or ".")
        include = arguments.get("include", "")

        if not target.exists():
            return ToolOutcome(
                content=f"error: path not found: {arguments.get('path', '.')}",
                is_error=True,
            )

        files: list[Path] = []
        if target.is_file():
            files = [target]
        else:
            for p in sorted(target.rglob("*")):
                if not p.is_file():
                    continue
                if any(part in self._SKIP_DIRS for part in p.parts):
                    continue
                if p.suffix == ".egg-info" or ".egg-info" in p.parts:
                    continue
                if include and not fnmatch.fnmatch(p.name, include):
                    continue
                files.append(p)

        if target.is_file() and include and not fnmatch.fnmatch(target.name, include):
            return ToolOutcome(content="(no matches)")

        matches: list[str] = []
        for file in files:
            try:
                text = file.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            try:
                rel = file.relative_to(self._cwd).as_posix()
            except ValueError:
                rel = str(file)
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    line = line.strip()
                    if len(line) > self._MAX_LINE_LEN:
                        line = line[: self._MAX_LINE_LEN] + "…"
                    matches.append(f"{rel}:{lineno}: {line}")
                    if len(matches) >= self._MAX_MATCHES:
                        matches.append(
                            f"... truncated at {self._MAX_MATCHES} matches"
                        )
                        return ToolOutcome(content="\n".join(matches))
        if not matches:
            return ToolOutcome(content="(no matches)")
        return ToolOutcome(content="\n".join(matches))


class _TextExtractor(HTMLParser):
    """Collect visible text from HTML, skipping script/style content."""

    _SKIP_TAGS = frozenset({"script", "style", "noscript"})

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in ("p", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in ("p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        raw = _html.unescape(raw)
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def _html_to_text(body: str) -> str:
    """Strip tags from an HTML document, returning readable plain text."""
    extractor = _TextExtractor()
    extractor.feed(body)
    return extractor.text()


class FetchTool:
    """Fetch a URL and return its content as text."""

    name = "fetch"
    description = (
        "Fetch a URL over HTTP(S) and return its content as text. "
        "HTML pages are converted to readable plain text."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "max_chars": {"type": "integer"},
        },
        "required": ["url"],
    }

    _DEFAULT_MAX_CHARS = 8000
    _MAX_CHARS_LIMIT = 50_000

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def run(self, arguments: dict) -> ToolOutcome:
        url = (arguments.get("url") or "").strip()
        if not url:
            return ToolOutcome(content="error: url is required", is_error=True)
        try:
            scheme = urlparse(url).scheme.lower()
        except ValueError as exc:
            return ToolOutcome(content=f"error: invalid url: {exc}", is_error=True)
        if scheme not in ("http", "https"):
            return ToolOutcome(
                content="error: only http(s) urls are supported", is_error=True
            )
        try:
            max_chars = int(arguments.get("max_chars", self._DEFAULT_MAX_CHARS))
        except (TypeError, ValueError):
            return ToolOutcome(content="error: max_chars must be an integer",
                               is_error=True)
        max_chars = max(1, min(max_chars, self._MAX_CHARS_LIMIT))

        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True
        )
        try:
            response = await client.get(
                url, headers={"User-Agent": "pico-agent/0.1"}
            )
        except httpx.HTTPError as exc:
            return ToolOutcome(content=f"error: request failed: {exc}", is_error=True)
        finally:
            if self._client is None:
                await client.aclose()

        if response.status_code >= 400:
            return ToolOutcome(
                content=f"error: HTTP {response.status_code} for {url}",
                is_error=True,
            )
        content_type = response.headers.get("content-type", "")
        body = response.text
        if "html" in content_type or body.lstrip().startswith("<"):
            body = _html_to_text(body)
        if not body.strip():
            return ToolOutcome(content="(empty response)")
        if len(body) > max_chars:
            body = body[:max_chars] + f"\n... truncated at {max_chars} chars"
        return ToolOutcome(content=body)


class _DDGResultParser(HTMLParser):
    """Parse DuckDuckGo ``/html/`` results into (title, url, snippet) triples."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._in_title = False
        self._in_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class", "") or ""
        if tag == "a" and "result__a" in classes:
            self._current = {"title": "", "url": dict(attrs).get("href", "") or "",
                             "snippet": ""}
            self._in_title = True
        elif tag == "a" and "result__snippet" in classes:
            self._in_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag != "a":
            return
        if self._in_title:
            self._in_title = False
        elif self._in_snippet:
            self._in_snippet = False
            if self._current and self._current["url"]:
                self._current["title"] = self._current["title"].strip()
                self._current["snippet"] = self._current["snippet"].strip()
                self.results.append(self._current)
                self._current = None

    def handle_data(self, data: str) -> None:
        if self._in_title and self._current is not None:
            self._current["title"] += data
        elif self._in_snippet and self._current is not None:
            self._current["snippet"] += data


class WebSearchTool:
    """Search the web (no API key required) and return ranked results."""

    name = "websearch"
    description = (
        "Search the web for a query and return ranked results "
        "(title, url, snippet)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "count": {"type": "integer"},
        },
        "required": ["query"],
    }

    _DEFAULT_COUNT = 5
    _MAX_COUNT = 10
    _SEARCH_URL = "https://html.duckduckgo.com/html/"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def run(self, arguments: dict) -> ToolOutcome:
        query = (arguments.get("query") or "").strip()
        if not query:
            return ToolOutcome(content="error: query is required", is_error=True)
        try:
            count = int(arguments.get("count", self._DEFAULT_COUNT))
        except (TypeError, ValueError):
            return ToolOutcome(content="error: count must be an integer",
                               is_error=True)
        count = max(1, min(count, self._MAX_COUNT))

        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0)
        )
        try:
            response = await client.post(
                self._SEARCH_URL,
                data={"q": query},
                headers={"User-Agent": "pico-agent/0.1"},
            )
        except httpx.HTTPError as exc:
            return ToolOutcome(content=f"error: search failed: {exc}", is_error=True)
        finally:
            if self._client is None:
                await client.aclose()

        if response.status_code >= 400:
            return ToolOutcome(
                content=f"error: search returned HTTP {response.status_code}",
                is_error=True,
            )
        parser = _DDGResultParser()
        parser.feed(response.text)
        results = parser.results[:count]
        if not results:
            return ToolOutcome(content="(no results)")
        lines: list[str] = []
        for i, item in enumerate(results, start=1):
            lines.append(f"{i}. {item['title']}\n   {item['url']}\n   {item['snippet']}")
        return ToolOutcome(content="\n".join(lines))


class ToolRegistry:
    """A name → tool mapping that also exposes tool definitions."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> Tool | None:
        """Remove a tool by name, returning it (or ``None`` if absent)."""
        return self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=t.name, description=t.description, input_schema=t.input_schema
            )
            for t in self._tools.values()
        ]

