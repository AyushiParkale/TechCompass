import socket
import unittest
from unittest.mock import patch

from backend.graph.external_knowledge import (
    ExternalSourceError,
    _ArticleParser,
    _configured_github_release_repositories,
    _fetch_arxiv,
    _fetch_crossref,
    _fetch_github_releases,
    _fetch_hacker_news,
    _fetch_google_news,
    _match_package_names,
    _parse_feed,
    _research_terms,
    sync_external_knowledge,
    preview_external_knowledge,
    fetch_external_source_tool,
    get_external_project_context,
    _upsert_items,
    _validate_public_https_url,
    external_evidence_for_packages,
)


class ExternalKnowledgeTests(unittest.TestCase):
    @patch("backend.graph.external_knowledge._upsert_items")
    @patch("backend.graph.external_knowledge._fetch_google_news")
    def test_live_source_tool_stores_results_and_returns_source_links(
        self, fetch, upsert,
    ):
        record = {
            "source": "Google News", "title": "Graph-based agents",
            "url": "https://news.google.com/article/1",
            "published_at": "2026-10-18", "item_type": "technology_news",
            "summary": "A project-relevant story.",
        }
        fetch.return_value = [{"record": record, "package": "", "version": ""}]
        upsert.return_value = {"items": 1, "package_links": 0,
                              "affected_version_links": 0, "references": 0}

        result = fetch_external_source_tool(
            object(), "google_news", query="graph-based agents"
        )

        self.assertEqual(result["fetched_records"], 1)
        self.assertEqual(result["records"][0]["url"], record["url"])
        self.assertEqual(result["ingestion"]["items"], 1)
        self.assertEqual(
            result["records"][0]["relevance"]["status"],
            "query_match_only",
        )
        self.assertEqual(
            result["records"][0]["project_impact"]["status"],
            "no_direct_project_impact_established",
        )
        upsert.assert_called_once()

    def test_project_context_reads_product_profile_and_repository_evidence(self):
        class Result:
            def __init__(self, rows):
                self.result_set = rows

        class Graph:
            def __init__(self):
                self.calls = 0

            def ro_query(self, _query):
                self.calls += 1
                rows = [
                    [["TechCompass", "https://github.com/acme/app", None,
                      "Help teams understand their codebase", None]],
                    [["TechCompass", "api", "backend/api"]],
                    [["api", "fastapi", "npm", "^1", "runtime"]],
                    [["Project Guide", "Goals", "Assist developers", 
                      "https://example.org/docs"]],
                    [[ ["Approach"], "Graph search", "Project Guide", "Goals",
                       "Connect code and documentation", "https://example.org/docs"]],
                    [["TechCompass", "Graph-backed repository assistant",
                      "Help developers understand analyzed projects",
                      [{"id": "explain-project-impact",
                        "text": "Separate relevance from project impact",
                        "source": "User requirement"}]]],
                ][self.calls - 1]
                return Result(rows)

        graph = Graph()
        context = get_external_project_context(graph)

        self.assertEqual(context["projects"][0]["business_goal"],
                         "Help teams understand their codebase")
        self.assertEqual(context["dependencies"][0]["package"], "fastapi")
        self.assertEqual(context["documentation"][0]["section"], "Goals")
        self.assertEqual(context["documented_concepts"][0]["concept"],
                         "Graph search")
        self.assertEqual(context["product_profile"]["business_goal"],
                         "Help developers understand analyzed projects")
        self.assertEqual(
            context["product_profile"]["objectives"][0]["id"],
            "explain-project-impact",
        )
        self.assertEqual(graph.calls, 6)

    @patch(
        "backend.graph.external_knowledge._request",
        return_value=(
            b'<rss><channel><item><title>New AI tool</title>'
            b'<link>https://example.org/ai</link><description>Details</description>'
            b'</item></channel></rss>',
            "application/rss+xml",
        ),
    )
    def test_fetches_google_news_rss_for_selected_query_and_locale(self, request):
        items = _fetch_google_news('"Hugging Face" OR open source', "en-GB", "GB")

        self.assertEqual(items[0]["record"]["source"], "Google News")
        self.assertEqual(items[0]["record"]["title"], "New AI tool")
        request.assert_called_once()
        url = request.call_args.args[0]
        self.assertIn("news.google.com/rss/search?", url)
        self.assertIn("hl=en-GB", url)
        self.assertIn("gl=GB", url)
        self.assertIn("ceid=GB%3Aen", url)

    @patch.dict(
        "os.environ",
        {
            "TECHCOMPASS_RESEARCH_TERMS": "",
            "TECHCOMPASS_GITHUB_RELEASE_REPOS": "",
            "TECHCOMPASS_RSS_FEEDS": "",
        },
    )
    @patch("backend.graph.external_knowledge._fetch_hacker_news")
    @patch("backend.graph.external_knowledge._fetch_crossref")
    @patch("backend.graph.external_knowledge._fetch_arxiv")
    @patch("backend.graph.external_knowledge._fetch_nvd")
    @patch("backend.graph.external_knowledge._fetch_github_advisories")
    @patch("backend.graph.external_knowledge._fetch_osv")
    def test_preview_fetches_samples_without_upserting(
        self, fetch_osv, fetch_github, fetch_nvd, fetch_arxiv,
        fetch_crossref, fetch_hn,
    ):
        class Result:
            def __init__(self, rows):
                self.result_set = rows

        class Graph:
            def ro_query(self, query):
                if "PackageVersion" in query:
                    return Result([["react", "19.0.0"]])
                return Result([["react", 2]])

        record = {
            "source": "OSV", "title": "Sample advisory",
            "url": "https://osv.dev/vulnerability/GHSA-sample",
            "published_at": "2026-01-01", "item_type": "vulnerability",
            "summary": "Sample summary",
        }
        fetch_osv.return_value = [{
            "record": record, "package": "react", "version": "19.0.0",
            "affected_range": "<19.0.1", "references": ["https://example.org"],
        }]
        for fetch in (fetch_github, fetch_nvd, fetch_arxiv, fetch_crossref, fetch_hn):
            fetch.return_value = []

        result = preview_external_knowledge(Graph())

        self.assertEqual(result["mode"], "read_only_preview")
        self.assertEqual(result["sources"]["osv"]["records"], 1)
        sample = result["sources"]["osv"]["preview"][0]
        self.assertEqual(sample["version"], "19.0.0")
        self.assertEqual(sample["references"], 1)
        self.assertEqual(result["sources"]["rss"]["status"], "skipped")
        fetch_github.assert_called_once_with(max_pages=1)

    def test_rejects_non_https_and_non_default_ports(self):
        with self.assertRaises(ExternalSourceError):
            _validate_public_https_url("http://example.com/article")
        with self.assertRaises(ExternalSourceError):
            _validate_public_https_url("https://example.com:8443/article")

    @patch(
        "backend.graph.external_knowledge.socket.getaddrinfo",
        return_value=[
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1", 443),
            )
        ],
    )
    def test_rejects_private_dns_results(self, _getaddrinfo):
        with self.assertRaises(ExternalSourceError):
            _validate_public_https_url("https://attacker.example/article")

    def test_article_parser_skips_script_content(self):
        parser = _ArticleParser()
        parser.feed(
            '<html><head><title>Dependency update</title>'
            '<meta name="description" content="Useful article"></head>'
            '<body><p>Read about LangChain.</p>'
            '<script>ignore this hidden text</script></body></html>'
        )

        self.assertEqual(" ".join(parser.title_parts), "Dependency update")
        self.assertEqual(parser.meta["description"], "Useful article")
        self.assertNotIn("ignore", " ".join(parser.text_parts))

    @patch(
        "backend.graph.external_knowledge.socket.getaddrinfo",
        return_value=[
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", 443),
            )
        ],
    )
    def test_parses_rss_items_without_fetching_article_urls(self, _getaddrinfo):
        feed = b"""\
        <rss><channel><item>
          <title>LangChain release</title>
          <link>https://example.com/post</link>
          <description>New approach</description>
          <guid>release-1</guid>
        </item></channel></rss>
        """

        entries = _parse_feed(feed, "https://example.com/feed.xml")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["record"]["title"], "LangChain release")
        self.assertEqual(entries[0]["record"]["url"], "https://example.com/post")

    def test_parses_arxiv_atom_entries_as_https_papers(self):
        feed = b"""\
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>http://arxiv.org/abs/2601.12345</id>
            <title>Research about LangChain</title>
            <summary>New retrieval methods.</summary>
            <published>2026-01-01T00:00:00Z</published>
            <link href="http://arxiv.org/abs/2601.12345"/>
          </entry>
        </feed>
        """

        entries = _parse_feed(
            feed,
            "https://arxiv.org/",
            source="arXiv",
            item_type="research_paper",
        )

        self.assertEqual(entries[0]["record"]["source"], "arXiv")
        self.assertEqual(entries[0]["record"]["item_type"], "research_paper")
        self.assertEqual(
            entries[0]["record"]["url"],
            "https://arxiv.org/abs/2601.12345",
        )

    @patch("backend.graph.external_knowledge.time.sleep")
    @patch("backend.graph.external_knowledge._request")
    def test_fetches_arxiv_papers_using_project_search_terms(
        self,
        request,
        _sleep,
    ):
        request.return_value = (
            b"""<feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>http://arxiv.org/abs/2601.12345</id>
                <title>Research about LangChain</title>
                <summary>New retrieval methods.</summary>
                <link href="http://arxiv.org/abs/2601.12345"/>
              </entry>
            </feed>""",
            "application/atom+xml",
        )

        entries = _fetch_arxiv(["langchain"])

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["record"]["source"], "arXiv")
        self.assertIn("search_query=all%3Alangchain", request.call_args.args[0])

    @patch("backend.graph.external_knowledge.time.sleep")
    @patch("backend.graph.external_knowledge._trusted_json")
    def test_fetches_crossref_metadata_and_strips_abstract_markup(
        self,
        trusted_json,
        _sleep,
    ):
        trusted_json.return_value = {
            "message": {
                "items": [{
                    "DOI": "10.1234/test",
                    "title": ["Research about LangChain"],
                    "URL": "https://doi.org/10.1234/test",
                    "abstract": "<jats:p>Useful <b>research</b>.</jats:p>",
                    "published": {"date-parts": [[2026, 3, 5]]},
                }]
            }
        }

        entries = _fetch_crossref(["langchain"])

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["record"]["source"], "Crossref")
        self.assertEqual(
            entries[0]["record"]["summary"],
            "Useful research.",
        )
        self.assertEqual(entries[0]["record"]["published_at"], "2026-3-5")

    @patch("backend.graph.external_knowledge._trusted_json")
    def test_fetches_hacker_news_stories(self, trusted_json):
        trusted_json.return_value = {
            "hits": [{
                "objectID": "123",
                "title": "LangChain engineering story",
                "url": "https://example.org/story",
                "story_text": "A new approach.",
                "created_at": "2026-10-01T00:00:00Z",
            }]
        }

        entries = _fetch_hacker_news(["langchain"])

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["record"]["source"], "Hacker News")
        self.assertEqual(entries[0]["record"]["item_type"], "engineering_story")
        self.assertIn("tags=story", trusted_json.call_args.args[0])

    @patch.dict(
        "os.environ",
        {
            "GITHUB_TOKEN": "test-token",
            "TECHCOMPASS_GITHUB_RELEASE_REPOS":
                "react=facebook/react,invalid,langchain=langchain-ai/langchain.git",
        },
    )
    @patch("backend.graph.external_knowledge._trusted_json")
    def test_fetches_mapped_github_releases_with_optional_token(
        self,
        trusted_json,
    ):
        trusted_json.return_value = [
            {
                "id": 1,
                "name": "React 19",
                "tag_name": "v19.0.0",
                "html_url": "https://github.com/facebook/react/releases/tag/v19",
                "published_at": "2026-01-01T00:00:00Z",
                "body": "New release",
                "draft": False,
                "prerelease": False,
            },
            {
                "id": 2,
                "name": "Draft",
                "draft": True,
                "prerelease": False,
            },
        ]

        configured = _configured_github_release_repositories()
        entries = _fetch_github_releases(configured)

        self.assertEqual(
            configured,
            [
                ("react", "facebook/react"),
                ("langchain", "langchain-ai/langchain"),
            ],
        )
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["record"]["source"], "GitHub Releases")
        self.assertEqual(entries[0]["match_type"], "repository_mapping")
        self.assertEqual(
            trusted_json.call_args.kwargs["headers"]["Authorization"],
            "Bearer test-token",
        )

    @patch.dict("os.environ", {"TECHCOMPASS_RESEARCH_TERMS": "LLM, RAG,LLM"})
    def test_research_terms_can_be_overridden_and_are_bounded(self):
        self.assertEqual(
            _research_terms(["react", "langchain"]),
            ["LLM", "RAG"],
        )

    @patch.dict(
        "os.environ",
        {
            "TECHCOMPASS_RESEARCH_TERMS": "",
            "TECHCOMPASS_GITHUB_RELEASE_REPOS": "",
        },
    )
    @patch("backend.graph.external_knowledge._upsert_items")
    @patch("backend.graph.external_knowledge._fetch_hacker_news")
    @patch("backend.graph.external_knowledge._fetch_crossref")
    @patch("backend.graph.external_knowledge._fetch_arxiv")
    @patch("backend.graph.external_knowledge._fetch_nvd")
    @patch("backend.graph.external_knowledge._fetch_github_advisories")
    @patch("backend.graph.external_knowledge._fetch_osv")
    def test_sync_runs_project_research_connectors_and_reports_optional_skips(
        self,
        fetch_osv,
        fetch_github,
        fetch_nvd,
        fetch_arxiv,
        fetch_crossref,
        fetch_hacker_news,
        upsert,
    ):
        class Result:
            def __init__(self, rows):
                self.result_set = rows

        class Graph:
            def ro_query(self, query):
                if "PackageVersion" in query:
                    return Result([])
                return Result([["langchain", 3], ["openai", 2]])

        for fetch in (
            fetch_osv,
            fetch_github,
            fetch_nvd,
            fetch_arxiv,
            fetch_crossref,
            fetch_hacker_news,
        ):
            fetch.return_value = []
        upsert.return_value = {
            "items": 0,
            "package_links": 0,
            "affected_version_links": 0,
            "references": 0,
        }

        result = sync_external_knowledge(Graph())

        self.assertEqual(fetch_arxiv.call_args.args[0], ["langchain", "openai"])
        self.assertEqual(
            fetch_crossref.call_args.args[0],
            ["langchain", "openai"],
        )
        self.assertEqual(
            fetch_hacker_news.call_args.args[0],
            ["langchain", "openai"],
        )
        self.assertEqual(result["sources"]["arxiv"]["status"], "ok")
        self.assertEqual(result["sources"]["crossref"]["status"], "ok")
        self.assertEqual(result["sources"]["hacker_news"]["status"], "ok")
        self.assertEqual(
            result["sources"]["github_releases"]["status"],
            "skipped",
        )

    def test_matches_package_names_as_tokens(self):
        matches = _match_package_names(
            "New @langchain/core integration, not @langchain/core-extra.",
            ["@langchain/core", "@langchain/core-extra", "x"],
        )
        longer_only = _match_package_names(
            "A mention of @langchain/core-extra only.",
            ["@langchain/core", "@langchain/core-extra"],
        )

        self.assertEqual(
            matches,
            ["@langchain/core", "@langchain/core-extra"],
        )
        self.assertEqual(longer_only, ["@langchain/core-extra"])

    def test_upserts_source_item_with_explicit_affected_version_evidence(self):
        class Result:
            def __init__(self, rows):
                self.result_set = rows

        class Graph:
            def __init__(self):
                self.queries = []

            def ro_query(self, query, params=None):
                if "MATCH (p:Package" in query:
                    return Result([["react"]])
                return Result([["react", "18.2.0"]])

            def query(self, query, params=None):
                self.queries.append((query, params))

        graph = Graph()
        item = {
            "record": {
                "id": "advisory-id",
                "external_id": "GHSA-test",
                "source": "OSV",
                "title": "React advisory",
                "url": "https://osv.dev/vulnerability/GHSA-test",
                "summary": "A test advisory",
                "published_at": "",
                "item_type": "vulnerability",
            },
            "package": "react",
            "version": "18.2.0",
            "affected_range": "",
            "references": [],
        }

        counts = _upsert_items(graph, [item])

        self.assertEqual(counts["items"], 1)
        self.assertEqual(counts["package_links"], 1)
        self.assertEqual(counts["affected_version_links"], 1)
        mention = next(
            params["rows"][0]
            for query, params in graph.queries
            if "MENTIONS_PACKAGE" in query
        )
        self.assertEqual(mention["match_type"], "osv_version_query")
        self.assertTrue(
            any("AFFECTS_VERSION" in query for query, _ in graph.queries)
        )

    def test_upserts_rss_package_mentions_as_text_matches(self):
        class Result:
            def __init__(self, rows):
                self.result_set = rows

        class Graph:
            def __init__(self):
                self.queries = []

            def ro_query(self, query, params=None):
                if "MATCH (p:Package" in query:
                    return Result([["@langchain/core"]])
                return Result([])

            def query(self, query, params=None):
                self.queries.append((query, params))

        graph = Graph()
        record = {
            "id": "article-id",
            "external_id": "feed-entry",
            "source": "RSS/Atom",
            "title": "New LangChain integration",
            "url": "https://example.org/article",
            "summary": "Read more about @langchain/core.",
            "published_at": "",
            "item_type": "article",
        }

        counts = _upsert_items(graph, [{
            "record": record,
            "package": "",
            "version": "",
            "affected_range": "",
            "references": [],
        }])

        mention = next(
            params["rows"][0]
            for query, params in graph.queries
            if "MENTIONS_PACKAGE" in query
        )
        self.assertEqual(counts["package_links"], 1)
        self.assertEqual(mention["package"], "@langchain/core")
        self.assertEqual(mention["match_type"], "title_summary_text_match")
        self.assertEqual(mention["affected_range"], "")

    def test_marks_exact_installed_version_vulnerability_matches(self):
        class Result:
            header = list(enumerate([
                "id",
                "title",
                "source",
                "item_type",
                "url",
                "published_at",
                "summary",
                "package",
                "affected_range",
                "affected_versions",
                "match_type",
            ]))
            result_set = [[
                "osv-1",
                "Advisory",
                "OSV",
                "vulnerability",
                "https://osv.dev/advisory",
                "",
                "Summary",
                "react",
                "",
                ["18.2.0"],
                "osv_version_query",
            ]]

        class Graph:
            def ro_query(self, _query, params):
                self.packages = params["packages"]
                return Result()

        graph = Graph()
        results = external_evidence_for_packages(
            graph,
            {"react": {"18.2.0", "18.3.0"}},
        )

        self.assertEqual(graph.packages, ["react"])
        self.assertTrue(results[0]["is_version_affected"])
        self.assertEqual(
            results[0]["installed_versions"],
            ["18.2.0", "18.3.0"],
        )


if __name__ == "__main__":
    unittest.main()
