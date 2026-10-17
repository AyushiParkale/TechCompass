
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.graph.query_service import (
    get_resolved_package_versions,
    trace_package_impact,
)


OSV_API = "https://api.osv.dev/v1"


def _post_osv(path: str, payload: dict) -> dict:
    request = Request(
        f"{OSV_API}/{path.lstrip('/')}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(
            f"OSV returned HTTP {exc.code}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(
            "Could not reach the OSV vulnerability service"
        ) from exc


def search_signals(
    package: str,
    ecosystem: str,
) -> dict:
    """Find OSV advisories for a package and ecosystem."""
    package = package.strip()
    ecosystem = ecosystem.strip()

    if not package or len(package) > 200:
        raise ValueError("Invalid package name")
    if not ecosystem or len(ecosystem) > 100:
        raise ValueError("Invalid ecosystem")

    result = _post_osv(
        "query",
        {
            "package": {
                "name": package,
                "ecosystem": ecosystem,
            }
        },
    )

    advisories = []
    for item in result.get("vulns", [])[:20]:
        advisory_id = item.get("id")
        if not advisory_id:
            continue

        affected_ranges = []
        for affected in item.get("affected", []):
            affected_package = affected.get("package", {})
            affected_ranges.append({
                "package": affected_package.get("name"),
                "ecosystem": affected_package.get("ecosystem"),
                "ranges": affected.get("ranges", []),
                "versions": affected.get("versions", []),
            })

        advisories.append({
            "id": advisory_id,
            "source": (
                f"https://osv.dev/vulnerability/{advisory_id}"
            ),
            "summary": item.get("summary"),
            "details": (item.get("details") or "")[:2000],
            "severity": item.get("severity", []),
            "affected": affected_ranges,
            "modified": item.get("modified"),
        })

    return {
        "source": "OSV",
        "package": package,
        "ecosystem": ecosystem,
        "advisories": advisories,
        "count": len(advisories),
        "note": (
            "Package-level advisory discovery does not by itself "
            "prove that the repository is affected."
        ),
    }


def trace_impact(graph, package: str) -> dict:
    """Retrieve graph impact and resolved lockfile versions."""
    package = package.strip()
    if not package or len(package) > 200:
        raise ValueError("Invalid package name")

    return {
        "package": package,
        "graph_impact": trace_package_impact(graph, package),
        "resolved_versions": get_resolved_package_versions(
            graph, package
        ),
    }


def verify_dependency_exposure(
    package: str,
    ecosystem: str,
    resolved_version: str,
    advisory_id: str,
) -> dict:
    """Check whether OSV associates an advisory with an exact version."""
    package = package.strip()
    ecosystem = ecosystem.strip()
    resolved_version = resolved_version.strip()
    advisory_id = advisory_id.strip()

    if not all([
        package,
        ecosystem,
        resolved_version,
        advisory_id,
    ]):
        raise ValueError("All verification fields are required")

    result = _post_osv(
        "query",
        {
            "package": {
                "name": package,
                "ecosystem": ecosystem,
            },
            "version": resolved_version,
        },
    )

    matches = [
        item
        for item in result.get("vulns", [])
        if item.get("id", "").casefold()
        == advisory_id.casefold()
    ]

    common = {
        "package": package,
        "ecosystem": ecosystem,
        "resolved_version": resolved_version,
        "advisory_id": advisory_id,
        "source": (
            f"https://osv.dev/vulnerability/{advisory_id}"
        ),
    }

    if matches:
        return {
            **common,
            "status": "confirmed",
            "reason": (
                "OSV returned this advisory for the specified "
                "package version."
            ),
        }

    return {
        **common,
        "status": "unknown",
        "reason": (
            "OSV did not return this advisory for the exact-version "
            "query. This is not sufficient by itself to prove the "
            "package is unaffected."
        ),
    }
