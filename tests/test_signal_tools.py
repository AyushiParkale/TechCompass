
from unittest.mock import patch

import pytest

from backend.agent import signal_tools


def test_search_signals_returns_advisories():
    fake_response = {
        "vulns": [
            {
                "id": "GHSA-test-1234",
                "summary": "Test vulnerability",
                "details": "A test advisory",
                "affected": [
                    {
                        "package": {
                            "name": "requests",
                            "ecosystem": "PyPI",
                        },
                        "ranges": [],
                        "versions": ["1.0.0"],
                    }
                ],
            }
        ]
    }

    with patch.object(
        signal_tools, "_post_osv", return_value=fake_response
    ) as mock_osv:
        result = signal_tools.search_signals("requests", "PyPI")

    assert result["source"] == "OSV"
    assert result["package"] == "requests"
    assert result["count"] == 1
    assert result["advisories"][0]["id"] == "GHSA-test-1234"
    mock_osv.assert_called_once()


def test_search_signals_rejects_empty_package():
    with pytest.raises(ValueError):
        signal_tools.search_signals("", "PyPI")


def test_verify_dependency_exposure_confirmed():
    fake_response = {
        "vulns": [{"id": "GHSA-test-1234"}]
    }

    with patch.object(
        signal_tools, "_post_osv", return_value=fake_response
    ) as mock_osv:
        result = signal_tools.verify_dependency_exposure(
            "requests",
            "PyPI",
            "1.0.0",
            "GHSA-test-1234",
        )

    assert result["status"] == "confirmed"
    assert result["resolved_version"] == "1.0.0"
    mock_osv.assert_called_once()


def test_verify_dependency_exposure_returns_unknown():
    with patch.object(
        signal_tools, "_post_osv", return_value={"vulns": []}
    ):
        result = signal_tools.verify_dependency_exposure(
            "requests",
            "PyPI",
            "1.0.0",
            "GHSA-test-1234",
        )

    assert result["status"] == "unknown"


def test_trace_impact_returns_graph_data():
    fake_impact = {"dependencies": ["requests"]}
    fake_versions = [{"package": "requests", "version": "1.0.0"}]

    with (
        patch.object(
            signal_tools, "trace_package_impact",
            return_value=fake_impact,
        ),
        patch.object(
            signal_tools, "get_resolved_package_versions",
            return_value=fake_versions,
        ),
    ):
        result = signal_tools.trace_impact(
            object(), "requests"
        )

    assert result["package"] == "requests"
    assert result["graph_impact"] == fake_impact
    assert result["resolved_versions"] == fake_versions
