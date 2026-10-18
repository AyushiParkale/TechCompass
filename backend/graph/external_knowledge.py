import hashlib
import http.client
import html
import ipaddress
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
import re
import socket
import ssl
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

logger = logging.getLogger("techcompass.external_knowledge")
MAX_RESPONSE_BYTES = 2_000_000
MAX_ARTICLE_CHARS = 8_000
USER_AGENT = "TechCompass/1.0 (+https://github.com/Shravani1383/TechCompass)"
TRUSTED_HOSTS = {
    "api.github.com",
    "api.crossref.org",
    "api.nvd.nist.gov",
    "api.osv.dev",
    "api.tavily.com",
    "export.arxiv.org",
    "hn.algolia.com",
    "services.nvd.nist.gov",
}
NVD_PACKAGE_LIMIT = 20
RESEARCH_TERM_LIMIT = 5
BATCH_SIZE = 250


class ExternalSourceError(RuntimeError):
    """Raised when an external source cannot be safely retrieved or parsed."""


class _ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self._in_title = False
        self._ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if tag == "meta":
            key = (
                attributes.get("property")
                or attributes.get("name")
                or ""
            ).lower()
            value = attributes.get("content", "").strip()
            if key and value:
                self.meta[key] = value

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        elif not self._ignored_depth:
            self.text_parts.append(text)


def _validate_public_https_url(url: str) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ExternalSourceError("URL is invalid") from exc

    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        raise ExternalSourceError(
            "Only public HTTPS URLs on the default port are allowed"
        )

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
        ".localhost"
    ):
        raise ExternalSourceError("Private and local network URLs are blocked")

    _resolve_public_addresses(hostname)
    return parsed


def _resolve_public_addresses(hostname: str) -> list[str]:
    try:
        addresses = socket.getaddrinfo(
            hostname,
            443,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ExternalSourceError("URL host could not be resolved") from exc
    if not addresses:
        raise ExternalSourceError("URL host has no public addresses")
    ips = []
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
            or ip.is_multicast
        ):
            raise ExternalSourceError(
                "Private and local network URLs are blocked"
            )
        ips.append(str(ip))
    return ips


def _request(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    trusted_hosts: set[str] | None = None,
) -> tuple[bytes, str]:
    parsed = urllib.parse.urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if trusted_hosts is None:
        _validate_public_https_url(url)
    elif hostname not in trusted_hosts:
        raise ExternalSourceError("Request host is not an approved source")
    addresses = _resolve_public_addresses(hostname)

    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, application/atom+xml, application/rss+xml, text/xml, text/html;q=0.8",
    }
    request_headers.update(headers or {})
    path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    connection = http.client.HTTPSConnection(
        hostname,
        443,
        timeout=15,
        context=ssl.create_default_context(),
    )
    try:
        raw_socket = socket.create_connection(
            (addresses[0], 443),
            timeout=15,
        )
        connection.sock = connection._context.wrap_socket(
            raw_socket,
            server_hostname=hostname,
        )
    except (OSError, ssl.SSLError) as exc:
        if "raw_socket" in locals():
            raw_socket.close()
        raise ExternalSourceError("External source connection failed") from exc
    try:
        connection.request(
            method,
            path,
            body=body,
            headers=request_headers,
        )
        response = connection.getresponse()
        if response.status < 200 or response.status >= 300:
            raise ExternalSourceError(
                f"External source returned HTTP {response.status}"
            )
        data = response.read(MAX_RESPONSE_BYTES + 1)
        content_type = response.getheader("Content-Type", "")
    except (OSError, http.client.HTTPException) as exc:
        raise ExternalSourceError("External source request failed") from exc
    finally:
        connection.close()

    if len(data) > MAX_RESPONSE_BYTES:
        raise ExternalSourceError("External source response exceeded 2 MB")
    return data, content_type


def _trusted_json(url: str, *, method="GET", payload=None, headers=None):
    request_headers = {"Accept": "application/json"}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    body = (
        json.dumps(payload).encode("utf-8")
        if payload is not None
        else None
    )
    data, _ = _request(
        url,
        method=method,
        body=body,
        headers=request_headers,
        trusted_hosts=TRUSTED_HOSTS,
    )
    try:
        return json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExternalSourceError("External source returned invalid JSON") from exc


def _stable_id(source: str, external_id: str) -> str:
    return hashlib.sha256(
        f"{source}:{external_id}".encode("utf-8")
    ).hexdigest()


def _record(
    *,
    source: str,
    external_id: str,
    title: str,
    url: str,
    summary: str = "",
    published_at: str = "",
    item_type: str = "article",
    retrieval_query: str = "",
    retrieval_score: float | None = None,
) -> dict[str, Any]:
    safe_url = url[:2000]
    return {
        "id": _stable_id(source, external_id or safe_url),
        "external_id": (external_id or safe_url)[:500],
        "source": source[:100],
        "title": (title or "Untitled source item")[:500],
        "url": safe_url,
        "summary": html.unescape(summary or "")[:MAX_ARTICLE_CHARS],
        "published_at": (published_at or "")[:80],
        "item_type": item_type,
        "retrieval_query": (retrieval_query or "")[:500],
        "retrieval_score": retrieval_score,
    }


def _parse_published_datetime(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _remove_out_of_window_years(query: str, *, days: int) -> str:
    """Prevent stale year terms from contradicting the search date filter."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    def keep_or_remove(match: re.Match[str]) -> str:
        year = int(match.group(1))
        month_text = match.group(2)
        if month_text:
            month = int(month_text)
            token_date = datetime(year, month, 1, tzinfo=timezone.utc)
        else:
            token_date = datetime(year, 1, 1, tzinfo=timezone.utc)
        # Year-only terms refer to a whole year. Keep the current year, but
        # drop older years that conflict with a bounded recent-news search.
        if month_text:
            return match.group(0) if token_date >= cutoff.replace(day=1) else " "
        return match.group(0) if year >= cutoff.year else " "

    cleaned = re.sub(
        r"\b((?:19|20)\d{2})(?:[-/.](0?[1-9]|1[0-2]))?\b",
        keep_or_remove,
        query,
    )
    return " ".join(cleaned.split())


def _fetch_web_search(
    query: str,
    *,
    topic: str = "news",
    days: int = 30,
    max_results: int = 5,
    include_domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Search the public web and return only dated results within the window."""
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise ExternalSourceError("TAVILY_API_KEY is not configured")
    query = query.strip()[:500]
    if not query:
        return []
    if topic not in {"general", "news"}:
        raise ValueError("Web search topic must be 'general' or 'news'")
    days = max(1, min(int(days), 365))
    max_results = max(1, min(int(max_results), 10))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    payload = {
        "query": query,
        "topic": topic,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_published_date": True,
        "filter_by_published_date": True,
        "start_date": cutoff.date().isoformat(),
        "end_date": now.date().isoformat(),
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    if include_domains:
        payload["include_domains"] = include_domains[:5]
    response = _trusted_json(
        "https://api.tavily.com/search",
        method="POST",
        payload=payload,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    items = []
    for result in response.get("results", [])[:max_results]:
        title = str(result.get("title") or "").strip()
        url = str(result.get("url") or "").strip()
        if not title or not url:
            continue
        try:
            _validate_public_https_url(url)
        except ExternalSourceError:
            logger.warning("Skipped web search result with unsafe URL")
            continue
        published_at = str(result.get("published_date") or "")
        published = _parse_published_datetime(published_at)
        if not published or not cutoff <= published <= now:
            continue
        try:
            score = float(result.get("score"))
        except (TypeError, ValueError):
            score = None
        items.append({
            "record": _record(
                source="Web Search (Tavily)",
                external_id=url,
                title=title,
                url=url,
                summary=str(result.get("content") or ""),
                published_at=published_at,
                item_type=("technology_news" if topic == "news" else "web_search_result"),
                retrieval_query=query,
                retrieval_score=score,
            ),
            "package": "",
            "version": "",
            "affected_range": "",
            "references": [],
        })
    return items


def _fetch_web_search_queries(
    queries: list[str], *, topic: str = "news", days: int = 30,
    include_domains: list[str] | None = None,
    progress_callback=None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run a small set of distinct searches, dedupe and rank the candidates."""
    unique_queries = list(dict.fromkeys(
        _remove_out_of_window_years(query.strip()[:500], days=days)
        for query in queries if query.strip()
    ))[:10]
    unique_queries = [query for query in unique_queries if query]
    by_url: dict[str, dict[str, Any]] = {}
    failures = []
    def fetch_one(query: str) -> tuple[str, list[dict[str, Any]], str | None]:
        try:
            return query, _fetch_web_search(
                query, topic=topic, days=days, max_results=5,
                include_domains=include_domains,
            ), None
        except ExternalSourceError as exc:
            return query, [], str(exc)

    # Each Tavily call is an independent HTTP read. Run a small bounded batch
    # concurrently, retaining input order so ranking and reports stay stable.
    with ThreadPoolExecutor(max_workers=min(5, len(unique_queries) or 1)) as pool:
        for completed, (query, items, failure) in enumerate(
            pool.map(fetch_one, unique_queries), start=1,
        ):
            if progress_callback:
                progress_callback({
                    "stage": "search",
                    "status": "running",
                    "detail": f"Checked search {completed} of {len(unique_queries)}: {query[:100]}",
                })
            if failure:
                failures.append(f"{query[:100]}: {failure}")
                continue
            for item in items:
                url = item["record"]["url"]
                previous = by_url.get(url)
                if (
                    previous is None
                    or (item["record"].get("retrieval_score") or 0)
                    > (previous["record"].get("retrieval_score") or 0)
                ):
                    by_url[url] = item
    ranked = sorted(
        by_url.values(),
        key=lambda item: (
            item["record"].get("retrieval_score") is not None,
            item["record"].get("retrieval_score") or 0,
            item["record"].get("published_at", ""),
        ),
        reverse=True,
    )
    if not ranked:
        for backend in ("duckduckgo", "bing"):
            if progress_callback:
                progress_callback({
                    "stage": "search",
                    "status": "running",
                    "detail": f"Tavily returned no dated results; checking {backend.title()}.",
                })
            try:
                fallback_items = _fetch_ddgs_queries(
                    unique_queries,
                    topic=topic,
                    days=days,
                    backend=backend,
                    include_domains=include_domains,
                    progress_callback=progress_callback,
                )
            except Exception as exc:
                logger.warning(
                    "%s web-search fallback failed (%s)",
                    backend,
                    type(exc).__name__,
                )
                failures.append(f"{backend}: {type(exc).__name__}")
                continue
            if fallback_items:
                ranked = fallback_items
                failures.append("Tavily returned no dated results; used " + backend + ".")
                break
    if not ranked and failures:
        raise ExternalSourceError("; ".join(failures)[:500])
    # Round-robin by retrieval query so one strong query cannot crowd out the
    # rest of the architecture areas from the report evidence.
    by_query: dict[str, list[dict[str, Any]]] = {}
    for item in ranked:
        by_query.setdefault(
            item["record"].get("retrieval_query", ""), []
        ).append(item)
    balanced = []
    seen_urls = set()
    for offset in range(5):
        for query in unique_queries:
            matches = by_query.get(query, [])
            if offset < len(matches):
                item = matches[offset]
                url = item["record"].get("url", "")
                if url and url not in seen_urls:
                    balanced.append(item)
                    seen_urls.add(url)
                if len(balanced) >= 20:
                    break
        if len(balanced) >= 20:
            break
    return balanced, failures


def _fetch_ddgs_queries(
    queries: list[str],
    *,
    topic: str,
    days: int,
    backend: str,
    include_domains: list[str] | None = None,
    progress_callback=None,
) -> list[dict[str, Any]]:
    """Use DuckDuckGo/Bing metasearch only after Tavily yields no dated results."""
    from ddgs import DDGS

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    timelimit = "d" if days <= 1 else "w" if days <= 7 else "m" if days <= 30 else None
    items_by_url: dict[str, dict[str, Any]] = {}
    with DDGS(timeout=8) as search:
        for index, query in enumerate(queries[:10], start=1):
            if topic == "news":
                results = search.news(
                    query=query,
                    timelimit=timelimit,
                    max_results=5,
                    backend=backend,
                )
            else:
                results = search.text(
                    query=query,
                    timelimit=timelimit,
                    max_results=5,
                    backend=backend,
                )
            if progress_callback:
                progress_callback({
                    "stage": "search",
                    "status": "running",
                    "detail": f"Checked {backend.title()} search {index} of {min(10, len(queries))}: {query[:100]}",
                })
            for result in results:
                title = str(result.get("title") or "").strip()
                url = str(result.get("url") or result.get("href") or "").strip()
                published_at = str(
                    result.get("date") or result.get("published") or ""
                ).strip()
                published = _parse_published_datetime(published_at)
                if not title or not url or not published or not cutoff <= published <= now:
                    continue
                try:
                    _validate_public_https_url(url)
                except ExternalSourceError:
                    continue
                host = urllib.parse.urlparse(url).hostname or ""
                if include_domains and not any(
                    host == domain or host.endswith("." + domain)
                    for domain in include_domains
                ):
                    continue
                items_by_url[url] = {
                    "record": _record(
                        source=("DuckDuckGo" if backend == "duckduckgo" else "Bing via DDGS"),
                        external_id=url,
                        title=title,
                        url=url,
                        summary=str(result.get("body") or result.get("excerpt") or ""),
                        published_at=published_at,
                        item_type=("technology_news" if topic == "news" else "web_search_result"),
                        retrieval_query=query,
                        retrieval_score=None,
                    ),
                    "package": "",
                    "version": "",
                    "affected_range": "",
                    "references": [],
                }
    return sorted(
        items_by_url.values(),
        key=lambda item: item["record"].get("published_at", ""),
        reverse=True,
    )


def _extract_advisory_references(advisory: dict[str, Any]) -> list[str]:
    references = advisory.get("references") or []
    return [
        ref["url"]
        for ref in references
        if isinstance(ref, dict) and ref.get("url")
    ][:20]


def _match_package_names(text: str, package_names: list[str]) -> list[str]:
    matches = []
    generic_names = {"open", "color", "fresh", "https", "http", "bl"}
    for package in package_names:
        normalized = package.casefold()
        # Very short/common package names collide with ordinary headline words
        # (for example, `open` in "Open WebUI"). Keep scoped names exact.
        if not normalized.startswith("@") and (
            len(normalized) < 5 or normalized in generic_names
        ):
            continue
        if re.search(
            rf"(?<![\w@/]){re.escape(normalized)}(?![\w/-])",
            text,
        ):
            matches.append(package)
    return matches


def _fetch_osv(package_versions: list[dict[str, str]]) -> list[dict[str, Any]]:
    items = []
    for offset in range(0, len(package_versions), 100):
        batch = package_versions[offset:offset + 100]
        response = _trusted_json(
            "https://api.osv.dev/v1/querybatch",
            method="POST",
            payload={
                "queries": [
                    {
                        "package": {
                            "name": item["name"],
                            "ecosystem": "npm",
                        },
                        "version": item["version"],
                    }
                    for item in batch
                ]
            },
        )
        results = response.get("results", [])
        if len(results) != len(batch):
            raise ExternalSourceError(
                "OSV returned a mismatched batch response"
            )
        for package_version, result in zip(batch, results):
            for advisory in result.get("vulns", []):
                advisory_id = advisory.get("id")
                if not advisory_id:
                    continue
                references = _extract_advisory_references(advisory)
                items.append({
                    "record": _record(
                        source="OSV",
                        external_id=advisory_id,
                        title=advisory.get("summary")
                        or advisory.get("details", "")[:300]
                        or advisory_id,
                        url=(
                            f"https://osv.dev/vulnerability/{advisory_id}"
                        ),
                        summary=advisory.get("details", ""),
                        published_at=advisory.get("published", ""),
                        item_type="vulnerability",
                    ),
                    "package": package_version["name"],
                    "version": package_version["version"],
                    "affected_range": "",
                    "references": references,
                })
    return items


def _fetch_github_advisories(max_pages: int = 3) -> list[dict[str, Any]]:
    items = []
    for page in range(1, max_pages + 1):
        url = (
            "https://api.github.com/advisories"
            f"?ecosystem=npm&per_page=100&page={page}"
        )
        advisories = _trusted_json(
            url,
            headers=_github_headers(),
        )
        if not isinstance(advisories, list):
            raise ExternalSourceError(
                "GitHub advisory endpoint returned an invalid response"
            )
        for advisory in advisories:
            ghsa_id = advisory.get("ghsa_id")
            if not ghsa_id:
                continue
            record = _record(
                source="GitHub Advisory Database",
                external_id=ghsa_id,
                title=advisory.get("summary") or ghsa_id,
                url=advisory.get("html_url")
                or f"https://github.com/advisories/{ghsa_id}",
                summary=advisory.get("description", ""),
                published_at=advisory.get("published_at", ""),
                item_type="vulnerability",
            )
            for vulnerability in advisory.get("vulnerabilities", []):
                package = vulnerability.get("package") or {}
                if package.get("ecosystem") != "npm" or not package.get("name"):
                    continue
                items.append({
                    "record": record,
                    "package": package["name"],
                    "version": "",
                    "affected_range": vulnerability.get(
                        "vulnerable_version_range", ""
                    ),
                    "references": [],
                })
        if len(advisories) < 100:
            break
    return items


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _configured_github_release_repositories() -> list[tuple[str, str]]:
    repositories = []
    configured = os.getenv("TECHCOMPASS_GITHUB_RELEASE_REPOS", "").strip()
    if configured:
        entries = configured.split(",")
    else:
        profile_path = os.path.join(
            os.path.dirname(__file__), "project_profile.json"
        )
        try:
            with open(profile_path, encoding="utf-8") as profile_file:
                profile = json.load(profile_file)
            entries = [
                f"{item.get('package', '')}={item.get('repository', '')}"
                for item in profile.get("source_strategy", {}).get(
                    "upstream_repositories", []
                )
            ]
        except (OSError, ValueError, TypeError):
            logger.warning("Unable to load TechCompass release source mappings")
            entries = []
    for entry in entries:
        package, separator, repository = entry.strip().partition("=")
        if not separator:
            continue
        package = package.strip()
        repository = repository.strip()
        if repository.endswith(".git"):
            repository = repository[:-4]
        if (
            package
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        ):
            repositories.append((package, repository))
    return repositories[:20]


def _analyzed_project_github_repository(graph: Any) -> list[tuple[str, str]]:
    """Resolve the analyzed project's own public GitHub repository from graph context."""
    result = graph.ro_query(
        """MATCH (p:Project)
        WHERE p.repository_url IS NOT NULL
        RETURN p.name AS name, p.repository_url AS repository_url
        ORDER BY p.name LIMIT 1"""
    )
    if not result.result_set:
        return []
    name, repository_url = result.result_set[0]
    try:
        parsed = urllib.parse.urlsplit(str(repository_url or ""))
    except ValueError:
        return []
    path = parsed.path.strip("/")
    if parsed.hostname != "github.com" or not path:
        return []
    path = path.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
        return []
    label = str(name or "analyzed-project").strip()[:100]
    return [(f"analyzed-project:{label}", path)]


def _configured_project_dependency_releases(
    graph: Any,
) -> list[tuple[str, str]]:
    """Keep configured release targets only when their package is in this graph."""
    mappings = _configured_github_release_repositories()
    if not mappings:
        return []
    result = graph.ro_query(
        "MATCH (p:Package) RETURN DISTINCT toLower(p.name) AS name LIMIT 5000"
    )
    graph_packages = {
        str(row[0]).casefold()
        for row in result.result_set if row and row[0]
    }
    return [
        (package, repository)
        for package, repository in mappings
        if package.casefold() in graph_packages
    ]


def _fetch_github_releases(
    repositories: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    items = []
    headers = _github_headers()
    for package, repository in repositories:
        encoded_repository = urllib.parse.quote(repository, safe="/")
        releases = _trusted_json(
            "https://api.github.com/repos/"
            f"{encoded_repository}/releases?per_page=10",
            headers=headers,
        )
        if not isinstance(releases, list):
            raise ExternalSourceError(
                "GitHub releases endpoint returned an invalid response"
            )
        for release in releases:
            if release.get("draft") or release.get("prerelease"):
                continue
            release_id = release.get("id")
            if release_id is None:
                continue
            tag = release.get("tag_name") or ""
            record = _record(
                source="GitHub Releases",
                external_id=f"{repository}:{release_id}",
                title=release.get("name") or tag or f"{repository} release",
                url=release.get("html_url")
                or f"https://github.com/{repository}/releases",
                summary=release.get("body", ""),
                published_at=(
                    release.get("published_at")
                    or release.get("created_at", "")
                ),
                item_type="release",
            )
            items.append({
                "record": record,
                "package": package,
                "version": "",
                "affected_range": "",
                "match_type": (
                    "analyzed_project_repository"
                    if package.startswith("analyzed-project:")
                    else "repository_mapping"
                ),
                "references": [],
            })
    return items


def _fetch_nvd(package_names: list[str]) -> list[dict[str, Any]]:
    api_key = os.getenv("NVD_API_KEY", "").strip()
    items = []
    package_limit = NVD_PACKAGE_LIMIT if api_key else 5
    for index, package_name in enumerate(package_names[:package_limit]):
        if index:
            time.sleep(0.7 if api_key else 6.1)
        params = urllib.parse.urlencode({
            "keywordSearch": package_name,
            "resultsPerPage": 100,
        })
        headers = {}
        if api_key:
            headers["apiKey"] = api_key
        response = _trusted_json(
            f"https://services.nvd.nist.gov/rest/json/cves/2.0?{params}",
            headers=headers,
        )
        for row in response.get("vulnerabilities", []):
            cve = row.get("cve", {})
            cve_id = cve.get("id")
            if not cve_id:
                continue
            descriptions = cve.get("descriptions", [])
            summary = next(
                (
                    item.get("value", "")
                    for item in descriptions
                    if item.get("lang") == "en"
                ),
                "",
            )
            items.append({
                "record": _record(
                    source="NVD",
                    external_id=cve_id,
                    title=cve_id,
                    url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                    summary=summary,
                    published_at=cve.get("published", ""),
                    item_type="vulnerability",
                ),
                "package": package_name,
                "version": "",
                "affected_range": "",
                "references": [],
            })
    return items


def _parse_feed(
    data: bytes,
    feed_url: str,
    *,
    source: str = "RSS/Atom",
    item_type: str = "article",
) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ExternalSourceError("RSS/Atom feed is invalid XML") from exc

    entries = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag not in {"item", "entry"}:
            continue
        fields = {}
        link = ""
        for child in element:
            child_tag = child.tag.rsplit("}", 1)[-1].lower()
            if child_tag == "link":
                link = child.attrib.get("href") or (child.text or "").strip()
            elif child_tag not in fields and child.text:
                fields[child_tag] = " ".join(child.text.split())
        title = fields.get("title", "")
        if not title:
            continue
        url = urllib.parse.urljoin(feed_url, link or feed_url)
        try:
            parsed = urllib.parse.urlsplit(url)
            if (
                parsed.scheme.lower() == "http"
                and parsed.hostname
                and (
                    parsed.hostname.lower() == "arxiv.org"
                    or parsed.hostname.lower().endswith(".arxiv.org")
                )
            ):
                parsed = parsed._replace(scheme="https")
            if (
                parsed.scheme.lower() != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ExternalSourceError(
                    "Feed article URL is not public HTTPS"
                )
        except ExternalSourceError:
            logger.warning("Skipped feed entry with unsafe article URL")
            continue
        summary = (
            fields.get("description")
            or fields.get("summary")
            or fields.get("content")
            or ""
        )
        external_id = fields.get("guid") or fields.get("id") or url
        entries.append({
            "record": _record(
                source=source,
                external_id=external_id,
                title=title,
                url=urllib.parse.urlunsplit(parsed),
                summary=summary,
                published_at=(
                    fields.get("pubdate")
                    or fields.get("published")
                    or fields.get("updated")
                    or ""
                ),
                item_type=item_type,
            ),
            "package": "",
            "version": "",
            "affected_range": "",
            "references": [],
        })
    return entries[:100]


def _research_terms(package_names: list[str]) -> list[str]:
    configured_terms = [
        term.strip()
        for term in os.getenv("TECHCOMPASS_RESEARCH_TERMS", "").split(",")
        if term.strip()
    ]
    # The project graph determines default search terms; no technology-name
    # allowlist filters the installed dependency set.
    terms = configured_terms or package_names
    return list(dict.fromkeys(terms))[:RESEARCH_TERM_LIMIT]


def _fetch_arxiv(terms: list[str]) -> list[dict[str, Any]]:
    entries = []
    for index, term in enumerate(terms):
        if index:
            time.sleep(3)
        params = urllib.parse.urlencode({
            "search_query": f"all:{term}",
            "start": 0,
            "max_results": 10,
            "sortBy": "relevance",
            "sortOrder": "descending",
        })
        data, _ = _request(
            f"https://export.arxiv.org/api/query?{params}",
            headers={"Accept": "application/atom+xml, application/xml"},
            trusted_hosts=TRUSTED_HOSTS,
        )
        entries.extend(_parse_feed(
            data,
            "https://arxiv.org/",
            source="arXiv",
            item_type="research_paper",
        ))
    unique = {item["record"]["id"]: item for item in entries}
    return list(unique.values())


def _plain_text(value: str) -> str:
    text = " ".join(
        html.unescape(re.sub(r"<[^>]*>", " ", value)).split()
    )
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


def _fetch_crossref(terms: list[str]) -> list[dict[str, Any]]:
    items = []
    for index, term in enumerate(terms):
        if index:
            time.sleep(1)
        params = urllib.parse.urlencode({
            "query.bibliographic": term,
            "rows": 10,
            "select": "DOI,title,URL,abstract,published,created",
        })
        response = _trusted_json(
            f"https://api.crossref.org/works?{params}",
            headers={"Accept": "application/json"},
        )
        message = response.get("message", {})
        for work in message.get("items", []):
            doi = work.get("DOI")
            titles = work.get("title") or []
            title = titles[0] if titles else doi or ""
            if not title:
                continue
            date_parts = (
                work.get("published", {}).get("date-parts")
                or work.get("created", {}).get("date-parts")
                or []
            )
            published_at = (
                "-".join(str(part) for part in date_parts[0])
                if date_parts and date_parts[0]
                else ""
            )
            url = work.get("URL") or (
                f"https://doi.org/{doi}" if doi else ""
            )
            if not url:
                continue
            record = _record(
                source="Crossref",
                external_id=doi or url,
                title=title,
                url=url,
                summary=_plain_text(work.get("abstract", "")),
                published_at=published_at,
                item_type="research_paper",
            )
            items.append({
                "record": record,
                "package": "",
                "version": "",
                "affected_range": "",
                "references": [],
            })
    unique = {item["record"]["id"]: item for item in items}
    return list(unique.values())


def _fetch_hacker_news(terms: list[str]) -> list[dict[str, Any]]:
    items = []
    for term in terms:
        params = urllib.parse.urlencode({
            "query": term,
            "tags": "story",
            "hitsPerPage": 10,
        })
        response = _trusted_json(
            f"https://hn.algolia.com/api/v1/search_by_date?{params}",
            headers={"Accept": "application/json"},
        )
        for story in response.get("hits", []):
            story_id = story.get("objectID")
            title = story.get("title") or ""
            if not story_id or not title:
                continue
            story_url = story.get("url") or (
                f"https://news.ycombinator.com/item?id={story_id}"
            )
            record = _record(
                source="Hacker News",
                external_id=story_id,
                title=title,
                url=story_url,
                summary=story.get("story_text") or "",
                published_at=story.get("created_at", ""),
                item_type="engineering_story",
            )
            items.append({
                "record": record,
                "package": "",
                "version": "",
                "affected_range": "",
                "references": [],
            })
    unique = {item["record"]["id"]: item for item in items}
    return list(unique.values())


def _fetch_article(url: str) -> dict[str, Any]:
    parsed = _validate_public_https_url(url)
    canonical_url = urllib.parse.urlunsplit(parsed)
    data, content_type = _request(canonical_url)
    if "html" not in content_type.lower() and not data.lstrip().startswith(b"<"):
        raise ExternalSourceError("Article URL must return HTML")
    parser = _ArticleParser()
    parser.feed(data.decode("utf-8", errors="replace"))
    title = (
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or " ".join(parser.title_parts)
        or canonical_url
    )
    summary = (
        parser.meta.get("description")
        or parser.meta.get("og:description")
        or " ".join(parser.text_parts)
    )
    return _record(
        source="Submitted article",
        external_id=canonical_url,
        title=title,
        url=canonical_url,
        summary=summary,
        item_type="article",
    )


def _upsert_items(graph: Any, items: list[dict[str, Any]]) -> dict[str, int]:
    packages_result = graph.ro_query(
        "MATCH (p:Package {ecosystem: 'npm'}) RETURN p.name AS name"
    )
    package_names = {
        row[0]
        for row in packages_result.result_set
        if row and row[0]
    }
    versions_result = graph.ro_query(
        """
        MATCH (v:PackageVersion {ecosystem: 'npm'})
        RETURN v.name AS name, v.version AS version
        """
    )
    known_versions = {
        (row[0], row[1])
        for row in versions_result.result_set
        if row and row[0] and row[1]
    }

    item_query = """
        UNWIND $rows AS row
        MERGE (i:ExternalItem {id: row.id})
        SET i.external_id = row.external_id,
            i.source = row.source,
            i.title = row.title,
            i.url = row.url,
            i.summary = row.summary,
            i.published_at = row.published_at,
            i.item_type = row.item_type,
            i.retrieval_query = row.retrieval_query,
            i.retrieval_score = row.retrieval_score,
            i.ingested_at = row.ingested_at
    """
    mention_query = """
        UNWIND $rows AS row
        MATCH (i:ExternalItem {id: row.id})
        MATCH (p:Package {name: row.package, ecosystem: 'npm'})
        MERGE (i)-[r:MENTIONS_PACKAGE]->(p)
        SET r.affected_range = row.affected_range,
            r.match_type = row.match_type
    """
    version_query = """
        UNWIND $rows AS row
        MATCH (i:ExternalItem {id: row.id})
        MATCH (v:PackageVersion {
            name: row.package,
            ecosystem: 'npm',
            version: row.version
        })
        MERGE (i)-[:AFFECTS_VERSION]->(v)
    """
    reference_query = """
        UNWIND $rows AS row
        MATCH (i:ExternalItem {id: row.id})
        MERGE (r:ExternalReference {url: row.url})
        MERGE (i)-[:HAS_REFERENCE]->(r)
    """

    unique_items: dict[str, dict[str, Any]] = {}
    linked_packages: dict[tuple[str, str], dict[str, str]] = {}
    affected_versions: set[tuple[str, str, str]] = set()
    references: set[tuple[str, str]] = set()
    for item in items:
        record = item["record"]
        unique_items[record["id"]] = record
        supplied_package = item.get("package", "")
        title = record["title"].casefold()
        matched_packages = (
            [supplied_package]
            if supplied_package in package_names
            else _match_package_names(title, package_names)
        )
        for package_name in matched_packages:
            version = (
                item.get("version", "")
                if package_name == supplied_package
                else ""
            )
            affected_range = (
                item.get("affected_range", "")
                if package_name == supplied_package
                else ""
            )
            if version and (package_name, version) in known_versions:
                match_type = "osv_version_query"
            elif affected_range:
                match_type = "advisory_affected_range"
            elif package_name == supplied_package:
                match_type = item.get(
                    "match_type",
                    "source_keyword_or_package_match",
                )
            else:
                match_type = "title_summary_text_match"
            linked_packages[(record["id"], package_name)] = {
                "affected_range": affected_range[:500],
                "match_type": match_type,
            }
            if version and (package_name, version) in known_versions:
                affected_versions.add((
                    record["id"],
                    package_name,
                    version,
                ))
        for reference_url in item.get("references", []):
            references.add((record["id"], reference_url))

    now = datetime.now(timezone.utc).isoformat()
    item_rows = [
        {**record, "ingested_at": now}
        for record in unique_items.values()
    ]
    mention_rows = [
        {"id": item_id, "package": package_name, **link}
        for (item_id, package_name), link in linked_packages.items()
    ]
    version_rows = [
        {"id": item_id, "package": package_name, "version": version}
        for item_id, package_name, version in affected_versions
    ]
    reference_rows = [
        {"id": item_id, "url": reference_url[:2000]}
        for item_id, reference_url in references
    ]
    for query, rows in (
        (item_query, item_rows),
        (mention_query, mention_rows),
        (version_query, version_rows),
        (reference_query, reference_rows),
    ):
        for start in range(0, len(rows), BATCH_SIZE):
            graph.query(
                query,
                {"rows": rows[start:start + BATCH_SIZE]},
            )
    return {
        "items": len(unique_items),
        "package_links": len(linked_packages),
        "affected_version_links": len(affected_versions),
        "references": len(references),
    }


def _configured_feeds() -> list[str]:
    return [
        url.strip()
        for url in os.getenv("TECHCOMPASS_RSS_FEEDS", "").split(",")
        if url.strip()
    ][:20]


def _fetch_google_news(
    query: str,
    language: str = "en-US",
    country: str = "US",
    days: int = 30,
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []
    days = max(1, min(int(days), 365))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    after_date = cutoff.date().isoformat()
    # Ask Google's RSS search to narrow the result and enforce the same bound
    # locally, since feed search operators do not guarantee strict recency.
    bounded_query = f"{query} after:{after_date}"
    language = language.strip()
    country = country.strip().upper()
    lang_code = language.split("-", 1)[0]
    params = urllib.parse.urlencode({
        "q": bounded_query,
        "hl": language,
        "gl": country,
        "ceid": f"{country}:{lang_code}",
    })
    url = f"https://news.google.com/rss/search?{params}"
    data, _ = _request(url)
    entries = _parse_feed(
        data,
        url,
        source="Google News",
        item_type="technology_news",
    )
    recent = []
    for entry in entries:
        published_at = entry.get("record", {}).get("published_at", "")
        try:
            published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            try:
                published = parsedate_to_datetime(published_at)
            except (TypeError, ValueError, OverflowError):
                continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        published = published.astimezone(timezone.utc)
        if cutoff <= published <= now:
            recent.append(entry)
    return recent


def get_external_project_context(
    graph: Any, *, discovery_only: bool = False,
) -> dict[str, Any]:
    """Read project goals, capabilities, implementation evidence, and docs for discovery."""
    project_query = (
        """MATCH (p:Project)
        WHERE toLower(p.name) CONTAINS 'anythingllm'
        RETURN p.name AS name, p.repository_url AS repository_url,
               p.description AS description, p.business_goal AS business_goal,
               p.official_domains AS official_domains
        LIMIT 1"""
        if discovery_only else
        """MATCH (p:Project)
        RETURN p.name AS name, p.repository_url AS repository_url,
               p.description AS description, p.business_goal AS business_goal,
               p.official_domains AS official_domains
        ORDER BY p.name LIMIT 20"""
    )
    project_result = graph.ro_query(project_query)
    project_rows = [
        {
            "name": row[0], "repository_url": row[1],
            "description": row[2], "business_goal": row[3],
            "official_domains": row[4] if len(row) > 4 and row[4] else [],
        }
        for row in project_result.result_set
    ]
    project_filter = "WHERE toLower(p.name) = toLower($project_name)" if discovery_only else ""
    project_params = {"project_name": project_rows[0]["name"]} if discovery_only and project_rows else {}
    if discovery_only and not project_rows:
        return {
            "product_profile": None, "projects": [], "project_goals": [],
            "project_objectives": [], "capabilities": [], "prior_assessments": [],
            "components": [], "dependencies": [], "documentation": [],
            "documented_concepts": [],
            "note": "No AnythingLLM project node was found in the graph.",
        }
    goal_result = graph.ro_query(
        """MATCH (p:Project)-[:HAS_GOAL]->(g:ProjectGoal)
        $PROJECT_FILTER
        OPTIONAL MATCH (g)-[:SUPPORTED_BY]->(e:Evidence)
        RETURN p.name AS project, g.text AS goal,
               collect(DISTINCT {source_url: e.source_url,
                                 source_path: e.source_path,
                                 evidence: substring(e.excerpt, 0, 250)}) AS evidence
        ORDER BY p.name LIMIT 20""".replace("$PROJECT_FILTER", project_filter), project_params
    )
    project_goals = [
        {"project": row[0], "goal": row[1],
         "evidence": [item for item in (row[2] or [])
                     if item and item.get("source_url")]}
        for row in goal_result.result_set if row and row[1]
    ]
    objective_result = graph.ro_query(
        """MATCH (p:Project)-[:HAS_OBJECTIVE]->(o:ProjectObjective)
        $PROJECT_FILTER
        OPTIONAL MATCH (o)-[:SUPPORTED_BY]->(e:Evidence)
        OPTIONAL MATCH (o)-[:ADVANCES]->(cap:ProjectCapability)
        RETURN p.name AS project, o.id AS id, o.text AS objective,
               collect(DISTINCT {document: e.document_title,
                                 section: e.section_heading,
                                 source_url: e.source_url}) AS evidence,
               collect(DISTINCT {id: cap.id, name: cap.name,
                                 description: cap.description}) AS capabilities
        ORDER BY p.name, o.id LIMIT 40""".replace("$PROJECT_FILTER", project_filter), project_params
    )
    project_objectives = [
        {"project": row[0], "id": row[1], "objective": row[2],
         "evidence": [item for item in (row[3] or [])
                     if item and item.get("source_url")][:2],
         "capabilities": [item for item in (row[4] or [])
                          if item and item.get("id")]}
        for row in objective_result.result_set if row and row[1]
    ]
    if discovery_only:
        components = []
        dependencies = []
        documentation = []
        concepts = []
    else:
        component_result = graph.ro_query(
            """MATCH (p:Project)-[:HAS_COMPONENT]->(c:RepoComponent)
            RETURN p.name AS project, c.name AS component, c.path AS path
            ORDER BY p.name, c.name LIMIT 50"""
        )
        components = [
            {"project": row[0], "component": row[1], "path": row[2]}
            for row in component_result.result_set
        ]
        dependency_result = graph.ro_query(
            """MATCH (c:RepoComponent)-[d:DEPENDS_ON]->(p:Package)
            WHERE d.dependency_type = 'runtime'
            RETURN c.name AS component, p.name AS package,
                   p.ecosystem AS ecosystem, d.declared_version AS declared_version,
                   d.dependency_type AS dependency_type
            ORDER BY p.name, c.name LIMIT 5"""
        )
        dependencies = [
            {
                "component": row[0], "package": row[1],
                "ecosystem": row[2], "declared_version": row[3],
                "dependency_type": row[4],
            }
            for row in dependency_result.result_set
        ]
        docs_result = graph.ro_query(
            """MATCH (d:Document)-[:HAS_SECTION]->(s:DocSection)
            RETURN d.title AS document, s.heading AS section,
                   s.source_url AS source_url
            ORDER BY d.title, s.heading LIMIT 3"""
        )
        documentation = [
            {
                "document": row[0], "section": row[1],
                "source_url": row[2],
            }
            for row in docs_result.result_set
        ]
        concept_result = graph.ro_query(
            """MATCH (c)-[:SUPPORTED_BY]->(e:Evidence)
            WHERE c:Technology OR c:Approach OR c:Constraint
            RETURN labels(c) AS types, c.name AS concept,
                   e.document_title AS document, e.section_heading AS section,
                   substring(e.excerpt, 0, 180) AS evidence, e.source_url AS source_url
            ORDER BY c.name, e.document_title LIMIT 10"""
        )
        concepts = [
            {
                "types": row[0], "concept": row[1], "document": row[2],
                "section": row[3], "evidence": str(row[4] or "")[:400],
                "source_url": row[5],
            }
            for row in concept_result.result_set
        ]
    capability_result = graph.ro_query(
        """MATCH (p:Project)-[:HAS_CAPABILITY]->(cap:ProjectCapability)
        $PROJECT_FILTER
        OPTIONAL MATCH (cap)-[:USES_PATTERN]->(pattern:ArchitecturePattern)
        OPTIONAL MATCH (cap)-[:USES_TECHNOLOGY]->(technology:Technology)
        OPTIONAL MATCH (cap)-[:SUPPORTED_BY]->(e:Evidence)
        OPTIONAL MATCH (cap)-[impl:IMPLEMENTED_BY]->(f:SourceFile)
        OPTIONAL MATCH (cap)-[component_link:RELATES_TO_COMPONENT]->(component:RepoComponent)
        RETURN cap.id AS id, cap.name AS capability,
               cap.description AS description,
               collect(DISTINCT pattern.name) AS architecture_patterns,
               collect(DISTINCT technology.name) AS technologies,
               collect(DISTINCT {document: e.document_title,
                                 section: e.section_heading,
                                 source_path: e.source_path,
                                 source_url: e.source_url,
                                 evidence: substring(e.excerpt, 0, 100)}) AS evidence,
               collect(DISTINCT {path: f.path,
                                 verification_status: impl.verification_status}) AS implementation_paths,
               collect(DISTINCT {name: component.name, path: component.path,
                                 verification_status: component_link.verification_status}) AS components
        ORDER BY cap.name LIMIT 40""".replace("$PROJECT_FILTER", project_filter), project_params
    )
    capabilities = []
    for row in capability_result.result_set:
        if not row or not row[0]:
            continue
        patterns = [item for item in (row[3] or []) if item]
        technologies = [item for item in (row[4] or []) if item]
        evidence = [
            item for item in (row[5] or [])
            if item and item.get("source_url")
        ]
        relevance_terms = {
            term for term in re.findall(
                r"[a-zA-Z0-9]+",
                " ".join([str(row[1] or ""), str(row[2] or ""), *patterns, *technologies]),
            )
            if len(term) > 3
        }
        evidence.sort(
            key=lambda item: sum(
                term.casefold() in " ".join(str(value or "") for value in item.values()).casefold()
                for term in relevance_terms
            ),
            reverse=True,
        )
        capabilities.append({
            "id": row[0], "capability": row[1], "description": row[2],
            "architecture_patterns": patterns,
            "technologies": technologies,
            "evidence": evidence[:3],
            "implementation_paths": [
                item for item in (row[6] or [])
                if item and item.get("path")
            ][:3],
            "components": [
                item for item in (row[7] or [])
                if item and item.get("name")
            ][:2],
        })
    prior_assessments = []
    if discovery_only:
        assessment_result = graph.ro_query(
            """MATCH (p:Project)-[:HAS_CAPABILITY]->(cap:ProjectCapability)
            <-[:ABOUT_CAPABILITY]-(a:ImpactAssessment)-[:ABOUT_ITEM]->(i:ExternalItem)
            $PROJECT_FILTER
            OPTIONAL MATCH (a)-[:GROUNDED_IN]->(e:Evidence)
            RETURN a.id AS id, a.assessed_at AS assessed_at,
                   a.relevance AS relevance, a.confidence AS confidence,
                   a.rationale AS rationale, a.potential_impact AS potential_impact,
                   a.next_step AS next_step, p.name AS project,
                   cap.id AS capability_id, cap.name AS capability,
                   i.title AS item_title, i.url AS item_url,
                   i.published_at AS item_published_at,
                   collect(DISTINCT {title: e.document_title,
                                     section: e.section_heading,
                                     source_url: e.source_url,
                                     source_path: e.source_path}) AS evidence
            ORDER BY a.assessed_at DESC LIMIT 8""".replace("$PROJECT_FILTER", project_filter), project_params
        )
        prior_assessments = [
            {
                "id": row[0], "assessed_at": row[1], "relevance": row[2],
                "confidence": row[3], "rationale": row[4],
                "potential_impact": row[5], "next_step": row[6],
                "project": row[7], "capability_id": row[8],
                "capability": row[9], "item_title": row[10],
                "item_url": row[11], "item_published_at": row[12],
                "evidence": [item for item in (row[13] or []) if item and item.get("source_url")],
            }
            for row in assessment_result.result_set if row and row[0]
        ]
    profile = None
    if not discovery_only:
        profile_result = graph.ro_query(
            """MATCH (p:ProductProfile {id: 'techcompass'})
            OPTIONAL MATCH (p)-[:HAS_OBJECTIVE]->(o:ProductObjective)
            RETURN p.name AS name, p.description AS description,
                   p.business_goal AS business_goal,
                   collect({id: o.id, text: o.text, source: o.source}) AS objectives
            LIMIT 1"""
        )
        if profile_result.result_set:
            row = profile_result.result_set[0]
            profile = {
                "name": row[0], "description": row[1], "business_goal": row[2],
            }
    return {
        "product_profile": profile,
        "projects": project_rows,
        "project_goals": project_goals,
        "project_objectives": project_objectives,
        "capabilities": capabilities,
        "prior_assessments": prior_assessments,
        "components": components,
        "dependencies": dependencies,
        "documentation": documentation,
        "documented_concepts": concepts,
        "note": (
            "Use capabilities, workflows, architecture patterns, technologies, "
            "and their cited project evidence as the primary discovery context. "
            "Packages are supporting evidence, not the default relevance model. "
            "Treat candidate source-file/component links as places to inspect, "
            "not proof of behavior. Keep the TechCompass product profile "
            "separate from the analyzed Project."
        ),
    }


def record_external_impact_assessment(
    graph: Any, *, item_id: str, capability_id: str, relevance: str,
    confidence: str, rationale: str, potential_impact: str, next_step: str,
) -> dict[str, Any]:
    """Persist a bounded, graph-validated assessment and return its evidence path."""
    if not re.fullmatch(r"[0-9a-f]{64}", item_id):
        raise ValueError("Unknown external item id")
    if relevance not in {"direct", "adjacent", "hypothesis"}:
        raise ValueError("Relevance must be direct, adjacent, or hypothesis")
    if confidence not in {"high", "medium", "low"}:
        raise ValueError("Confidence must be high, medium, or low")
    rationale = rationale.strip()[:700]
    potential_impact = potential_impact.strip()[:900]
    next_step = next_step.strip()[:400]
    if not rationale or not potential_impact or not next_step:
        raise ValueError("Assessment rationale, impact, and next step are required")

    assessment_id = _stable_id(
        "impact-assessment", f"{item_id}:{capability_id}"
    )
    result = graph.query(
        """MATCH (project:Project)-[:HAS_CAPABILITY]->(cap:ProjectCapability {id: $capability_id})
        MATCH (item:ExternalItem {id: $item_id})
        MERGE (assessment:ImpactAssessment {id: $assessment_id})
        SET assessment.relevance = $relevance,
            assessment.confidence = $confidence,
            assessment.rationale = $rationale,
            assessment.potential_impact = $potential_impact,
            assessment.next_step = $next_step,
            assessment.assessed_at = $assessed_at,
            assessment.assessor = 'TechCompass external-update agent'
        MERGE (assessment)-[:ABOUT_ITEM]->(item)
        MERGE (assessment)-[:ABOUT_CAPABILITY]->(cap)
        MERGE (assessment)-[:ASSESSES_PROJECT]->(project)
        WITH project, cap, item, assessment
        OPTIONAL MATCH (cap)-[:SUPPORTED_BY]->(e:Evidence)
        FOREACH (evidence IN CASE WHEN e IS NULL THEN [] ELSE [e] END |
            MERGE (assessment)-[:GROUNDED_IN]->(e))
        WITH project, cap, item, assessment,
             collect(DISTINCT {title: e.document_title,
                               section: e.section_heading,
                               source_url: e.source_url,
                               source_path: e.source_path,
                               excerpt: substring(e.excerpt, 0, 240)}) AS evidence
        OPTIONAL MATCH (cap)-[impl:IMPLEMENTED_BY]->(file:SourceFile)
        OPTIONAL MATCH (cap)-[component_link:RELATES_TO_COMPONENT]->(component:RepoComponent)
        WITH project, cap, item, assessment, evidence,
             collect(DISTINCT {path: file.path,
                               verification_status: impl.verification_status}) AS implementation_paths,
             collect(DISTINCT {name: component.name, path: component.path,
                               verification_status: component_link.verification_status}) AS components
        MATCH path = (project)-[:HAS_CAPABILITY]->(cap)
                      <-[:ABOUT_CAPABILITY]-(assessment)-[:ABOUT_ITEM]->(item)
        RETURN assessment.id AS id, assessment.assessed_at AS assessed_at,
               [node IN nodes(path) | {labels: labels(node),
                   id: coalesce(node.id, node.name),
                   name: coalesce(node.name, node.title), title: node.title,
                   url: node.url}] AS path,
               evidence, cap.name AS capability,
               cap.description AS capability_description,
               implementation_paths, components""",
        {
            "capability_id": capability_id,
            "item_id": item_id,
            "assessment_id": assessment_id,
            "relevance": relevance,
            "confidence": confidence,
            "rationale": rationale,
            "potential_impact": potential_impact,
            "next_step": next_step,
            "assessed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    if not result.result_set:
        raise ValueError("The external item or capability is not present in this project graph")
    row = result.result_set[0]
    return {
        "stored": True,
        "assessment_id": row[0],
        "assessed_at": row[1],
        "path": row[2] or [],
        "project_evidence": row[3] or [],
        "project_evidence_count": len(row[3] or []),
        "capability": {
            "name": row[4],
            "description": row[5],
            "implementation_paths": [
                item for item in (row[6] or []) if item and item.get("path")
            ],
            "components": [
                item for item in (row[7] or []) if item and item.get("name")
            ],
            "implementation_path_count": len([
                item for item in (row[6] or []) if item and item.get("path")
            ]),
        },
        "note": "This saved assessment is a reasoned relevance hypothesis, not proof that the external change has been implemented.",
    }


def fetch_external_source_tool(
    graph: Any,
    source: str,
    *,
    query: str = "",
    package: str = "",
    version: str = "",
    language: str = "en-US",
    country: str = "US",
    days: int = 30,
    topic: str = "news",
    domains: str = "",
    exclude_domains: list[str] | None = None,
    exclude_url_prefixes: list[str] | None = None,
    exclude_title_terms: list[str] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    """Fetch one bounded live-source result set and store it as graph evidence."""
    query = query.strip()[:1200 if source == "web_search" else 200]
    package = package.strip()[:200]
    items: list[dict[str, Any]]
    source_failures: list[str] = []
    excluded_record_count = 0
    excluded_out_of_scope_count = 0
    if source == "osv":
        if not package or not version:
            raise ValueError("OSV requires an exact graph package and version")
        installed = graph.ro_query(
            """MATCH (v:PackageVersion {ecosystem: 'npm', name: $name,
                                            version: $version})
            RETURN v.name LIMIT 1""",
            {"name": package, "version": version},
        )
        if not installed.result_set:
            raise ValueError("Package version is not present in the project graph")
        items = _fetch_osv([{"name": package, "version": version}])
    elif source == "github_advisories":
        if not package:
            raise ValueError("GitHub advisory search requires a graph package")
        items = [
            item for item in _fetch_github_advisories(max_pages=1)
            if item.get("package", "").casefold() == package.casefold()
        ]
    elif source == "nvd":
        if not query:
            raise ValueError("NVD search requires a graph-derived query")
        items = _fetch_nvd([query])
    elif source == "google_news":
        if not query:
            raise ValueError("Google News search requires a graph-derived query")
        items = _fetch_google_news(query, language, country, days)
    elif source == "web_search":
        if not query:
            raise ValueError("Web search requires a graph-derived query")
        include_domains = [
            host.strip().casefold()
            for host in domains.split(",") if host.strip()
        ]
        if len(include_domains) > 5 or any(
            not re.fullmatch(
                r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
                host,
            )
            for host in include_domains
        ):
            raise ValueError("Search domains must be up to five hostnames")
        items, source_failures = _fetch_web_search_queries(
            query.splitlines(), topic=topic, days=days,
            include_domains=include_domains or None,
            progress_callback=progress_callback,
        )
        blocked_domains = [
            str(host).strip().casefold().lstrip(".")
            for host in (exclude_domains or []) if str(host).strip()
        ]
        blocked_prefixes = [
            str(prefix).strip().rstrip("/").casefold()
            for prefix in (exclude_url_prefixes or []) if str(prefix).strip()
        ]
        if len(blocked_domains) > 20 or len(blocked_prefixes) > 20:
            raise ValueError("Search exclusion list is too long")
        title_terms = [
            str(term).strip().casefold()
            for term in (exclude_title_terms or []) if str(term).strip()
        ]
        if len(title_terms) > 30 or any(len(term) > 120 for term in title_terms):
            raise ValueError("Search title exclusion list is too long")
        for host in blocked_domains:
            if not re.fullmatch(
                r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
                host,
            ):
                raise ValueError("Excluded domains must be hostnames")
        for prefix in blocked_prefixes:
            parsed_prefix = urllib.parse.urlsplit(prefix)
            if parsed_prefix.scheme != "https" or not parsed_prefix.hostname:
                raise ValueError("Excluded URL prefixes must be HTTPS URLs")

        retained = []
        for item in items:
            record_url = str(item.get("record", {}).get("url") or "").casefold()
            host = (urllib.parse.urlsplit(record_url).hostname or "").casefold()
            title = str(item.get("record", {}).get("title") or "").casefold()
            excluded = any(
                host == domain or host.endswith("." + domain)
                for domain in blocked_domains
            ) or any(
                record_url == prefix or record_url.startswith(prefix + "/")
                for prefix in blocked_prefixes
            )
            if excluded:
                excluded_record_count += 1
            elif any(term in title for term in title_terms):
                excluded_out_of_scope_count += 1
            else:
                retained.append(item)
        items = retained
    elif source == "arxiv":
        if not query:
            raise ValueError("arXiv search requires a graph-derived query")
        items = _fetch_arxiv([query])
    elif source == "crossref":
        if not query:
            raise ValueError("Crossref search requires a graph-derived query")
        items = _fetch_crossref([query])
    elif source == "hacker_news":
        if not query:
            raise ValueError("Hacker News search requires a graph-derived query")
        items = _fetch_hacker_news([query])
    elif source == "github_releases":
        mappings = _configured_project_dependency_releases(graph)
        if package:
            mappings = [item for item in mappings if item[0].casefold() == package.casefold()]
        else:
            mappings = _analyzed_project_github_repository(graph) + mappings
        items = _fetch_github_releases(mappings[:3]) if mappings else []
    elif source == "rss":
        items = []
        for feed_url in _configured_feeds()[:5]:
            data, _ = _request(feed_url)
            items.extend(_parse_feed(data, feed_url))
        items = items[:50]
    else:
        raise ValueError("Unknown external source")

    items = items[:50]
    if progress_callback and source == "web_search" and items:
        progress_callback({
            "stage": "search",
            "status": "running",
            "detail": f"Saving {len(items)} dated source records and their links to FalkorDB.",
        })
    counts = _upsert_items(graph, items) if items else {
        "items": 0, "package_links": 0,
        "affected_version_links": 0, "references": 0,
    }
    # Keep search relevance separate from project impact. A text match can make
    # an item worth reviewing, but only graph dependency/import evidence can
    # establish a concrete connection to the analyzed codebase.
    package_result = graph.ro_query(
        "MATCH (p:Package) RETURN DISTINCT p.name AS name ORDER BY name LIMIT 2000"
    ) if items and hasattr(graph, "ro_query") else None
    graph_packages = {
        row[0] for row in (package_result.result_set if package_result else [])
        if row and row[0]
    }
    graph_entities: list[dict[str, str]] = [
        {"type": "package", "name": name} for name in graph_packages
    ]
    if items and hasattr(graph, "ro_query"):
        profile_result = graph.ro_query(
            """MATCH (p:ProductProfile {id: 'techcompass'})
            OPTIONAL MATCH (p)-[:HAS_OBJECTIVE]->(o:ProductObjective)
            RETURN p.name AS name, p.description AS description,
                   p.business_goal AS business_goal,
                   collect(o.text) AS objectives LIMIT 1"""
        )
        if profile_result.result_set:
            row = profile_result.result_set[0]
            graph_entities.extend(
                {"type": kind, "name": str(value)[:500]}
                for kind, value in zip(
                    ("product_profile", "product_description", "business_goal"),
                    row[:3],
                ) if value
            )
            graph_entities.extend(
                {"type": "product_objective", "name": str(value)[:500]}
                for value in (row[3] or []) if value
            )
        concept_result = graph.ro_query(
            """MATCH (c)-[:SUPPORTED_BY]->(:Evidence)
            WHERE c:Technology OR c:Approach OR c:Constraint
            RETURN DISTINCT labels(c)[0] AS type, c.name AS name LIMIT 500"""
        )
        graph_entities.extend(
            {"type": str(row[0] or "concept"), "name": row[1]}
            for row in concept_result.result_set if row and row[1]
        )
        project_result = graph.ro_query(
            """MATCH (p:Project)
            RETURN p.name AS name, p.description AS description,
                   p.business_goal AS business_goal, p.objectives AS objectives
            LIMIT 20"""
        )
        for row in project_result.result_set:
            if not row:
                continue
            for kind, value in zip(
                (
                    "project", "project_description",
                    "project_business_goal", "project_objective",
                ),
                row,
            ):
                values = value if isinstance(value, list) else [value]
                graph_entities.extend(
                    {"type": kind, "name": str(text)[:300]}
                    for text in values if text
                )
        capability_result = graph.ro_query(
            """MATCH (p:Project)-[:HAS_CAPABILITY]->(cap:ProjectCapability)
            OPTIONAL MATCH (cap)-[:USES_PATTERN]->(pattern:ArchitecturePattern)
            OPTIONAL MATCH (cap)-[:USES_TECHNOLOGY]->(technology:Technology)
            RETURN cap.name AS capability, cap.description AS description,
                   collect(DISTINCT pattern.name) AS patterns,
                   collect(DISTINCT technology.name) AS technologies
            ORDER BY capability LIMIT 40"""
        )
        for row in capability_result.result_set:
            if not row:
                continue
            graph_entities.extend(
                {"type": kind, "name": str(value)[:300]}
                for kind, value in (
                    ("project_capability", row[0]),
                    ("project_capability_description", row[1]),
                ) if value
            )
            graph_entities.extend(
                {"type": "architecture_pattern", "name": str(value)[:200]}
                for value in (row[2] or []) if value
            )
            graph_entities.extend(
                {"type": "project_technology", "name": str(value)[:200]}
                for value in (row[3] or []) if value
            )
    preview = []
    for item in items[:10]:
        record = item.get("record", {})
        title = record.get("title", "")
        summary = record.get("summary", "")
        content = f"{title} {summary}".casefold()
        exact_package = item.get("package", "")
        title_package_matches = _match_package_names(
            title.casefold(), graph_packages
        )
        matched_packages = ([exact_package] if exact_package in graph_packages else [])
        matched_packages.extend(
            name for name in title_package_matches
            if name not in matched_packages
        )
        matched_entities = [
            entity for entity in graph_entities
            if entity["type"] != "package"
            and entity["type"] not in {
                "product_profile", "product_description", "business_goal",
                "product_objective",
            }
            and entity["name"].casefold() in content
        ]
        for name in matched_packages:
            if not any(e["type"] == "package" and e["name"] == name
                       for e in matched_entities):
                matched_entities.append({"type": "package", "name": name})
        relevance = {
            "status": (
                "analyzed_project_release"
                if item.get("match_type") == "analyzed_project_repository" else
                "mapped_dependency_release"
                if item.get("match_type") == "repository_mapping" else
                "direct_package_name_in_title" if title_package_matches else
                "graph_concept_match" if matched_entities else
                "query_match_only"
            ),
            "matched_graph_entities": matched_entities[:12],
            "matched_graph_packages": matched_packages,
            "basis": (
                "The item came from the GitHub repository_url of the analyzed Project. It is project-level context; review the release notes to explain code impact."
                if item.get("match_type") == "analyzed_project_repository" else
                "The item came from an upstream repository explicitly mapped to a package. Confirm the package/component exists in the analyzed repository before claiming implementation impact."
                if item.get("match_type") == "repository_mapping" else
                "The headline contains an exact package name present in the analyzed repository graph. This is a relevance lead, not proof of impact."
                if title_package_matches else
                "The record mentions a documented project concept; this is contextual relevance, not proof of implementation impact."
                if matched_entities else
                "The record was returned for the graph-derived search query; "
                "no exact project package match was found."
            ),
        }
        impact_evidence = []
        for matched in matched_packages[:3]:
            declared = graph.ro_query(
                """MATCH (c:RepoComponent)-[d:DEPENDS_ON]->(p:Package)
                WHERE toLower(p.name) = toLower($package)
                RETURN c.name AS component, c.path AS path,
                       d.declared_version AS declared_version,
                       d.dependency_type AS dependency_type LIMIT 50""",
                {"package": matched},
            )
            imports = graph.ro_query(
                """MATCH (f:SourceFile)-[:IMPORTS_PACKAGE]->(p:Package)
                WHERE toLower(p.name) = toLower($package)
                RETURN f.path AS source_file LIMIT 50""",
                {"package": matched},
            )
            impact_evidence.append({
                "package": matched,
                "declared_dependencies": [
                    {"component": row[0], "path": row[1],
                     "declared_version": row[2], "dependency_type": row[3]}
                    for row in declared.result_set
                ],
                "direct_imports": [row[0] for row in imports.result_set if row and row[0]],
            })
        has_runtime_dependency = any(
            dependency.get("dependency_type") == "runtime"
            for evidence in impact_evidence
            for dependency in evidence["declared_dependencies"]
        )
        has_development_dependency = any(
            dependency.get("dependency_type") in {"development", "dev"}
            for evidence in impact_evidence
            for dependency in evidence["declared_dependencies"]
        )
        has_import = any(x["direct_imports"] for x in impact_evidence)
        impact_status = (
            "runtime_dependency_and_import" if has_runtime_dependency and has_import else
            "runtime_dependency" if has_runtime_dependency else
            "direct_import" if has_import else
            "development_tooling_dependency" if has_development_dependency else
            "no_direct_project_impact_established"
        )
        is_security_source = source in {"osv", "github_advisories", "nvd"}
        reported_impact_status = (
            impact_status if is_security_source
            else "dependency_path_is_context_only"
            if has_runtime_dependency or has_import or has_development_dependency
            else "no_direct_project_impact_established"
        )
        preview.append({
            "id": record.get("id", ""),
            "source": record.get("source", source),
            "title": title,
            "url": record.get("url", ""),
            "published_at": record.get("published_at", ""),
            "publication_date_status": (
                "within_requested_window" if source == "web_search" else "not_checked"
            ),
            "retrieval_query": record.get("retrieval_query", ""),
            "retrieval_score": record.get("retrieval_score"),
            "item_type": record.get("item_type", ""),
            "summary": summary[:240],
            "package": item.get("package", ""),
            "version": item.get("version", ""),
            "relevance": relevance,
            "project_impact": {
                "status": reported_impact_status,
                "dependency_path_status": impact_status,
                "evidence": impact_evidence,
                "interpretation": (
                    "This external discovery result has a graph-matched dependency/import, "
                    "but that connection alone does not show that the update affects "
                    "AnythingLLM. Assess the update against the project's documented "
                    "capabilities and architecture evidence."
                if not is_security_source and (has_runtime_dependency or has_import) else
                    "Graph edges connect a matched runtime package or direct import "
                    "to project code; this does not by itself prove the update causes impact."
                if has_runtime_dependency or has_import else
                "Only a development dependency path was found. This indicates "
                "repository tooling, not impact on AnythingLLM's runtime."
                if has_development_dependency else
                    "No dependency or direct-import path was found for this item; "
                    "treat it as context to investigate, not a demonstrated project impact."
                ),
            },
        })
    response = {
        "source": source,
        "query": query or package,
        "fetched_records": len(items),
        "ingestion": counts,
        "records": preview,
    }
    if source == "web_search":
        cleaned_queries = []
        for line in query.splitlines():
            if not line.strip():
                continue
            cleaned = _remove_out_of_window_years(line.strip()[:500], days=days)
            if cleaned and cleaned not in cleaned_queries:
                cleaned_queries.append(cleaned)
        response["queries_used"] = cleaned_queries[:10]
        response["query"] = "\n".join(response["queries_used"])
        response["query_result_counts"] = [
            {
                "query": searched_query,
                "result_count": sum(
                    item.get("record", {}).get("retrieval_query") == searched_query
                    for item in items
                ),
            }
            for searched_query in response["queries_used"]
        ]
        response["excluded_project_source_count"] = excluded_record_count
        response["excluded_out_of_scope_count"] = excluded_out_of_scope_count
        window_end = datetime.now(timezone.utc)
        window_start = window_end - timedelta(days=max(1, min(int(days), 365)))
        response["date_window"] = {
            "days": max(1, min(int(days), 365)),
            "from": window_start.date().isoformat(),
            "through": window_end.date().isoformat(),
            "basis": (
                "This filters source publication dates. A publication date is "
                "not a launch date; confirm actual launch or availability "
                "inside this window on an official source."
            ),
        }
    if source_failures:
        response["source_failures"] = source_failures
    return response


def preview_external_knowledge(
    graph: Any,
    *,
    google_news: dict[str, str] | None = None,
    size: str = "small",
) -> dict[str, Any]:
    """Fetch a small live sample per source without writing to the graph."""
    versions_result = graph.ro_query(
        """MATCH (v:PackageVersion {ecosystem: 'npm'})
        RETURN DISTINCT v.name AS name, v.version AS version LIMIT 2000"""
    )
    versions = [
        {"name": row[0], "version": row[1]}
        for row in versions_result.result_set if row and row[0] and row[1]
    ]
    names_result = graph.ro_query(
        """MATCH (c:RepoComponent)-[dependency:DEPENDS_ON]->(p:Package)
        WHERE dependency.dependency_type = 'runtime'
        RETURN p.name AS name, count(DISTINCT c) AS component_count
        ORDER BY component_count DESC, name LIMIT 100"""
    )
    package_names = [
        row[0] for row in names_result.result_set if row and row[0]
    ]
    terms = _research_terms(package_names)
    size = size if size in {"small", "medium"} else "small"
    limits = {
        "small": {"versions": 5, "packages": 1, "terms": 1, "github_pages": 1, "releases": 1},
        "medium": {"versions": 20, "packages": 3, "terms": 3, "github_pages": 2, "releases": 3},
    }[size]
    google_news = google_news or {}
    news_query = str(google_news.get("query", "")).strip()[:250]
    news_language = str(google_news.get("language", "en-US")).strip()[:12]
    news_country = str(google_news.get("country", "US")).strip().upper()[:2]
    context = {
        "preview_size": size,
        "installed_versions_checked": versions[:limits["versions"]],
        "package_keywords": package_names[:limits["packages"]],
        "research_terms": terms[:limits["terms"]],
        "google_news": {
            "query": news_query, "language": news_language,
            "country": news_country,
        },
        "configured_feeds": _configured_feeds(),
        "analyzed_project_repository": [
            {"project": project.split(":", 1)[1], "repository": repo}
            for project, repo in _analyzed_project_github_repository(graph)
        ],
        "configured_release_repositories": [
            {"package": package, "repository": repo}
            for package, repo in _configured_project_dependency_releases(graph)
        ],
    }
    sources: dict[str, Any] = {}
    jobs = [
        ("osv", lambda: _fetch_osv(versions[:limits["versions"]]), bool(versions)),
        ("github", lambda: _fetch_github_advisories(max_pages=limits["github_pages"]), True),
        ("nvd", lambda: _fetch_nvd(package_names[:limits["packages"]]), bool(package_names)),
        ("arxiv", lambda: _fetch_arxiv(terms[:limits["terms"]]), bool(terms)),
        ("crossref", lambda: _fetch_crossref(terms[:limits["terms"]]), bool(terms)),
        ("hacker_news", lambda: _fetch_hacker_news(terms[:limits["terms"]]), bool(terms)),
    ]
    releases = (
        _analyzed_project_github_repository(graph)
        + _configured_project_dependency_releases(graph)
    )[:limits["releases"]]
    jobs.append(("github_releases", lambda: _fetch_github_releases(releases), bool(releases)))

    def summarize(name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        samples = []
        for item in items[:3]:
            record = item.get("record", {})
            samples.append({
                "source": record.get("source", name),
                "title": record.get("title", ""),
                "url": record.get("url", ""),
                "published_at": record.get("published_at", ""),
                "item_type": record.get("item_type", ""),
                "summary": record.get("summary", "")[:500],
                "package": item.get("package", ""),
                "version": item.get("version", ""),
                "affected_range": item.get("affected_range", ""),
                "references": len(item.get("references", [])),
            })
        return {"status": "ok", "records": len(items), "preview": samples}

    for name, fetch, available in jobs:
        if not available:
            sources[name] = {"status": "skipped", "reason": "No matching project context is available"}
            continue
        try:
            sources[name] = summarize(name, fetch())
        except ExternalSourceError as exc:
            sources[name] = {"status": "error", "error": str(exc)}
        except Exception:
            logger.exception("%s preview failed unexpectedly", name)
            sources[name] = {"status": "error", "error": "Unexpected source processing error"}

    feeds = []
    for url in context["configured_feeds"]:
        try:
            data, _ = _request(url)
            feeds.append({"url": url, **summarize("rss", _parse_feed(data, url))})
        except ExternalSourceError as exc:
            feeds.append({"url": url, "status": "error", "error": str(exc)})
        except Exception:
            logger.exception("RSS/Atom preview failed unexpectedly")
            feeds.append({"url": url, "status": "error", "error": "Unexpected feed processing error"})
    sources["rss"] = ({
        "status": "ok",
        "records": sum(feed.get("records", 0) for feed in feeds),
        "feeds": feeds,
        "preview": [item for feed in feeds for item in feed.get("preview", [])][:3],
    } if feeds else {
        "status": "skipped", "reason": "No feeds configured"
    })
    if news_query:
        try:
            sources["google_news"] = summarize(
                "google_news",
                _fetch_google_news(news_query, news_language, news_country),
            )
        except ExternalSourceError as exc:
            sources["google_news"] = {"status": "error", "error": str(exc)}
        except Exception:
            logger.exception("Google News preview failed unexpectedly")
            sources["google_news"] = {"status": "error", "error": "Unexpected source processing error"}
    else:
        sources["google_news"] = {
            "status": "skipped", "reason": "Enter a Google News search query"
        }
    return {"mode": "read_only_preview", "context": context, "sources": sources}


def sync_external_knowledge(
    graph: Any,
    *,
    google_news: dict[str, str] | None = None,
) -> dict[str, Any]:
    package_versions_result = graph.ro_query(
        """
        MATCH (v:PackageVersion {ecosystem: 'npm'})
        RETURN DISTINCT v.name AS name, v.version AS version
        LIMIT 2000
        """
    )
    package_versions = [
        {"name": row[0], "version": row[1]}
        for row in package_versions_result.result_set
        if row and row[0] and row[1]
    ]
    package_names_result = graph.ro_query(
        """
        MATCH (c:RepoComponent)-[dependency:DEPENDS_ON]->(p:Package)
        WHERE dependency.dependency_type = 'runtime'
        RETURN p.name AS name, count(DISTINCT c) AS component_count
        ORDER BY component_count DESC, name
        LIMIT 100
        """
    )
    package_names = [
        row[0] for row in package_names_result.result_set if row and row[0]
    ]
    research_terms = _research_terms(package_names)

    results: dict[str, Any] = {}
    collected: list[dict[str, Any]] = []
    connectors = [
        ("osv", lambda: _fetch_osv(package_versions)),
        ("github", _fetch_github_advisories),
        ("nvd", lambda: _fetch_nvd(package_names)),
        ("arxiv", lambda: _fetch_arxiv(research_terms)),
        ("crossref", lambda: _fetch_crossref(research_terms)),
        ("hacker_news", lambda: _fetch_hacker_news(research_terms)),
    ]
    release_repositories = (
        _analyzed_project_github_repository(graph)
        + _configured_project_dependency_releases(graph)
    )
    if release_repositories:
        connectors.append((
            "github_releases",
            lambda: _fetch_github_releases(release_repositories),
        ))
    else:
        results["github_releases"] = {
            "status": "skipped",
            "reason": (
                "The analyzed Project has no GitHub repository_url and no "
                "TECHCOMPASS_GITHUB_RELEASE_REPOS mappings are configured"
            ),
        }
    for name, fetch in connectors:
        try:
            fetched = fetch()
            collected.extend(fetched)
            results[name] = {"status": "ok", "records": len(fetched)}
        except ExternalSourceError as exc:
            logger.warning("%s sync failed: %s", name, exc)
            results[name] = {"status": "error", "error": str(exc)}
        except Exception:
            logger.exception("%s sync failed unexpectedly", name)
            results[name] = {
                "status": "error",
                "error": "Unexpected source processing error",
            }

    feed_results = []
    for feed_url in _configured_feeds():
        try:
            data, _ = _request(feed_url)
            feed_items = _parse_feed(data, feed_url)
            collected.extend(feed_items)
            feed_results.append({
                "url": feed_url,
                "status": "ok",
                "records": len(feed_items),
            })
        except ExternalSourceError as exc:
            feed_results.append({
                "url": feed_url,
                "status": "error",
                "error": str(exc),
            })
        except Exception:
            logger.exception("RSS/Atom feed sync failed unexpectedly")
            feed_results.append({
                "url": feed_url,
                "status": "error",
                "error": "Unexpected feed processing error",
            })
    results["rss"] = feed_results

    google_news = google_news or {}
    news_query = str(google_news.get("query", "")).strip()[:250]
    if news_query:
        try:
            news_items = _fetch_google_news(
                news_query,
                str(google_news.get("language", "en-US"))[:12],
                str(google_news.get("country", "US")).upper()[:2],
            )
            collected.extend(news_items)
            results["google_news"] = {
                "status": "ok", "records": len(news_items),
                "query": news_query,
            }
        except ExternalSourceError as exc:
            results["google_news"] = {"status": "error", "error": str(exc)}
        except Exception:
            logger.exception("Google News sync failed unexpectedly")
            results["google_news"] = {
                "status": "error", "error": "Unexpected source processing error"
            }
    else:
        results["google_news"] = {
            "status": "skipped", "reason": "No search query provided"
        }

    counts = _upsert_items(graph, collected)
    return {
        "sources": results,
        "totals": {
            "fetched_records": len(collected),
            **counts,
        },
    }


def ingest_article_url(graph: Any, url: str) -> dict[str, Any]:
    record = _fetch_article(url)
    packages_result = graph.ro_query(
        "MATCH (p:Package {ecosystem: 'npm'}) RETURN p.name AS name"
    )
    package_names = [
        row[0] for row in packages_result.result_set if row and row[0]
    ]
    package_matches = _match_package_names(
        record["title"].casefold(), package_names
    )
    records = [
        {
            "record": record,
            "package": package,
            "version": "",
            "affected_range": "",
            "references": [],
        }
        for package in package_matches
    ]
    counts = _upsert_items(graph, records or [{
        "record": record,
        "package": "",
        "version": "",
        "affected_range": "",
        "references": [],
    }])
    return {
        **record,
        "matched_packages": package_matches,
        "ingestion": counts,
    }


def search_external_knowledge(
    graph: Any,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    terms = [
        term.casefold()
        for term in re.findall(r"[a-zA-Z0-9@./_-]+", query)
        if len(term) > 1
    ][:8]
    if not terms:
        return []
    result = graph.ro_query(
        """
        MATCH (i:ExternalItem)
        OPTIONAL MATCH (i)-[r:MENTIONS_PACKAGE]->(p:Package)
        WHERE any(term IN $terms
            WHERE toLower(coalesce(i.title, '')) CONTAINS term
               OR toLower(coalesce(i.summary, '')) CONTAINS term
               OR toLower(coalesce(p.name, '')) CONTAINS term)
        OPTIONAL MATCH (i)-[:AFFECTS_VERSION]->(v:PackageVersion)
        RETURN i.title AS title,
               i.source AS source,
               i.item_type AS item_type,
               i.url AS url,
               i.published_at AS published_at,
               i.summary AS summary,
               i.retrieval_query AS retrieval_query,
               i.retrieval_score AS retrieval_score,
               collect(DISTINCT p.name) AS packages,
               collect(DISTINCT r.match_type) AS match_types,
               collect(DISTINCT r.affected_range) AS affected_ranges,
               collect(DISTINCT v.version) AS affected_versions
        ORDER BY i.published_at DESC, i.title
        LIMIT $limit
        """,
        {"terms": terms, "limit": min(max(limit, 1), 50)},
    )
    columns = [column[1] for column in result.header]
    return [
        dict(zip(columns, row))
        for row in result.result_set
    ]


def external_evidence_for_packages(
    graph: Any,
    package_versions: dict[str, set[str]],
) -> list[dict[str, Any]]:
    if not package_versions:
        return []
    result = graph.ro_query(
        """
        MATCH (i:ExternalItem)-[mention:MENTIONS_PACKAGE]->(p:Package)
        WHERE p.name IN $packages
        OPTIONAL MATCH (i)-[:AFFECTS_VERSION]->(v:PackageVersion)
        WHERE v.name = p.name
        RETURN DISTINCT i.id AS id,
               i.title AS title,
               i.source AS source,
               i.item_type AS item_type,
               i.url AS url,
               i.published_at AS published_at,
               i.summary AS summary,
               i.retrieval_query AS retrieval_query,
               i.retrieval_score AS retrieval_score,
               p.name AS package,
               mention.affected_range AS affected_range,
               collect(DISTINCT v.version) AS affected_versions,
               mention.match_type AS match_type
        ORDER BY i.published_at DESC
        LIMIT 100
        """,
        {"packages": list(package_versions)},
    )
    columns = [column[1] for column in result.header]
    rows = [
        dict(zip(columns, row))
        for row in result.result_set
    ]
    for row in rows:
        installed = package_versions.get(row.get("package"), set())
        affected = set(row.get("affected_versions") or [])
        row["installed_versions"] = sorted(installed)
        row["is_version_affected"] = bool(installed & affected)
    return rows
