import hashlib
import http.client
import html
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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
    }


def _extract_advisory_references(advisory: dict[str, Any]) -> list[str]:
    references = advisory.get("references") or []
    return [
        ref["url"]
        for ref in references
        if isinstance(ref, dict) and ref.get("url")
    ][:20]


def _match_package_names(text: str, package_names: list[str]) -> list[str]:
    matches = []
    for package in package_names:
        normalized = package.casefold()
        if len(normalized) < 3 and not normalized.startswith("@"):
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
    for entry in os.getenv("TECHCOMPASS_GITHUB_RELEASE_REPOS", "").split(","):
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
                "match_type": "repository_mapping",
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
        text = f"{record['title']} {record['summary']}".casefold()
        matched_packages = (
            [supplied_package]
            if supplied_package in package_names
            else _match_package_names(text, package_names)
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
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []
    language = language.strip()
    country = country.strip().upper()
    lang_code = language.split("-", 1)[0]
    params = urllib.parse.urlencode({
        "q": query,
        "hl": language,
        "gl": country,
        "ceid": f"{country}:{lang_code}",
    })
    url = f"https://news.google.com/rss/search?{params}"
    data, _ = _request(url)
    return _parse_feed(
        data,
        url,
        source="Google News",
        item_type="technology_news",
    )


def get_external_project_context(graph: Any) -> dict[str, Any]:
    """Read project goals, components, dependencies, and docs for live search planning."""
    project_result = graph.ro_query(
        """MATCH (p:Project)
        RETURN p.name AS name, p.repository_url AS repository_url,
               p.description AS description, p.business_goal AS business_goal,
               p.objectives AS objectives
        ORDER BY p.name LIMIT 20"""
    )
    project_rows = [
        {
            "name": row[0], "repository_url": row[1],
            "description": row[2], "business_goal": row[3],
            "objectives": row[4],
        }
        for row in project_result.result_set
    ]
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
        RETURN c.name AS component, p.name AS package,
               p.ecosystem AS ecosystem, d.declared_version AS declared_version,
               d.dependency_type AS dependency_type
        ORDER BY d.dependency_type, p.name, c.name LIMIT 100"""
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
               s.content AS content, s.source_url AS source_url
        ORDER BY d.title, s.heading LIMIT 40"""
    )
    documentation = [
        {
            "document": row[0], "section": row[1],
            "excerpt": str(row[2] or "")[:500], "source_url": row[3],
        }
        for row in docs_result.result_set
    ]
    concept_result = graph.ro_query(
        """MATCH (c)-[:SUPPORTED_BY]->(e:Evidence)
        WHERE c:Technology OR c:Approach OR c:Constraint
        RETURN labels(c) AS types, c.name AS concept,
               e.document_title AS document, e.section_heading AS section,
               e.excerpt AS evidence, e.source_url AS source_url
        ORDER BY c.name, e.document_title LIMIT 40"""
    )
    concepts = [
        {
            "types": row[0], "concept": row[1], "document": row[2],
            "section": row[3], "evidence": str(row[4] or "")[:400],
            "source_url": row[5],
        }
        for row in concept_result.result_set
    ]
    profile_result = graph.ro_query(
        """MATCH (p:ProductProfile {id: 'techcompass'})
        OPTIONAL MATCH (p)-[:HAS_OBJECTIVE]->(o:ProductObjective)
        RETURN p.name AS name, p.description AS description,
               p.business_goal AS business_goal,
               collect({id: o.id, text: o.text, source: o.source}) AS objectives
        LIMIT 1"""
    )
    profile = None
    if profile_result.result_set:
        row = profile_result.result_set[0]
        profile = {
            "name": row[0], "description": row[1], "business_goal": row[2],
            "objectives": [item for item in (row[3] or []) if item.get("id")],
        }
    return {
        "product_profile": profile,
        "projects": project_rows,
        "components": components,
        "dependencies": dependencies,
        "documentation": documentation,
        "documented_concepts": concepts,
        "note": (
            "Use only what these graph records establish. The TechCompass "
            "profile states the product purpose; the Project node describes "
            "the analyzed repository. Keep those roles separate."
        ),
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
) -> dict[str, Any]:
    """Fetch one bounded live-source result set and store it as graph evidence."""
    query = query.strip()[:200]
    package = package.strip()[:200]
    items: list[dict[str, Any]]
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
        items = _fetch_google_news(query, language, country)
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
        mappings = _configured_github_release_repositories()
        if package:
            mappings = [item for item in mappings if item[0].casefold() == package.casefold()]
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
                ("project", "description", "business_goal", "objective"), row
            ):
                values = value if isinstance(value, list) else [value]
                graph_entities.extend(
                    {"type": kind, "name": str(text)[:300]}
                    for text in values if text
                )
    preview = []
    for item in items[:8]:
        record = item.get("record", {})
        title = record.get("title", "")
        summary = record.get("summary", "")
        content = f"{title} {summary}".casefold()
        exact_package = item.get("package", "")
        matched_packages = ([exact_package] if exact_package in graph_packages else [])
        matched_packages.extend(
            name for name in _match_package_names(content, graph_packages)
            if name not in matched_packages
        )
        matched_entities = [
            entity for entity in graph_entities
            if entity["name"].casefold() in content
        ]
        for name in matched_packages:
            if not any(e["type"] == "package" and e["name"] == name
                       for e in matched_entities):
                matched_entities.append({"type": "package", "name": name})
        relevance = {
            "status": "project_entity_match" if matched_entities else "query_match_only",
            "matched_graph_entities": matched_entities[:12],
            "matched_graph_packages": matched_packages,
            "basis": (
                "The title or summary contains a named entity or objective stored in the project graph."
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
        has_dependency = any(x["declared_dependencies"] for x in impact_evidence)
        has_import = any(x["direct_imports"] for x in impact_evidence)
        impact_status = (
            "project_dependency_and_import" if has_dependency and has_import else
            "project_dependency" if has_dependency else
            "direct_import" if has_import else
            "no_direct_project_impact_established"
        )
        preview.append({
            "source": record.get("source", source),
            "title": title,
            "url": record.get("url", ""),
            "published_at": record.get("published_at", ""),
            "item_type": record.get("item_type", ""),
            "summary": summary[:400],
            "package": item.get("package", ""),
            "version": item.get("version", ""),
            "relevance": relevance,
            "project_impact": {
                "status": impact_status,
                "evidence": impact_evidence,
                "interpretation": (
                    "Graph edges connect a matched package to project code; "
                    "this does not by itself prove the news item causes an impact."
                    if has_dependency or has_import else
                    "No dependency or direct-import path was found for this item; "
                    "treat it as context to investigate, not a demonstrated project impact."
                ),
            },
        })
    return {
        "source": source,
        "query": query or package,
        "fetched_records": len(items),
        "ingestion": counts,
        "records": preview,
    }


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
        "configured_release_repositories": [
            {"package": package, "repository": repo}
            for package, repo in _configured_github_release_repositories()
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
    releases = _configured_github_release_repositories()[:limits["releases"]]
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
    release_repositories = _configured_github_release_repositories()
    if release_repositories:
        connectors.append((
            "github_releases",
            lambda: _fetch_github_releases(release_repositories),
        ))
    else:
        results["github_releases"] = {
            "status": "skipped",
            "reason": "Configure TECHCOMPASS_GITHUB_RELEASE_REPOS",
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
    text = f"{record['title']} {record['summary']}".casefold()
    package_matches = _match_package_names(text, package_names)
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
