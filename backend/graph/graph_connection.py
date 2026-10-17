import ipaddress
import os
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def connect_graph():
    host = os.getenv("FALKORDB_HOST", "").strip()
    password = os.getenv("FALKORDB_PASSWORD", "")
    port = int(os.getenv("FALKORDB_PORT", "6379"))
    graph_name = os.getenv("FALKORDB_GRAPH", "techcompass")

    if not host or not password:
        raise RuntimeError(
            "Set FALKORDB_HOST and FALKORDB_PASSWORD in .env"
        )
    if is_local_host(host):
        raise RuntimeError(
            "FALKORDB_HOST must point to the deployed FalkorDB instance; "
            "local database connections are disabled"
        )

    db = FalkorDB(
        host=host,
        port=port,
        password=password,
        **connection_options(),
    )
    return db, db.select_graph(graph_name)


def is_local_host(host: str) -> bool:
    normalized_host = host.lower().rstrip(".")
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        return True

    try:
        return ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        return False


def connection_options():
    host = os.getenv("FALKORDB_HOST", "").strip()
    password = os.getenv("FALKORDB_PASSWORD", "")
    if not host or not password:
        raise RuntimeError(
            "Set FALKORDB_HOST and FALKORDB_PASSWORD in .env"
        )
    if is_local_host(host):
        raise RuntimeError(
            "FALKORDB_HOST must point to the deployed FalkorDB instance; "
            "local database connections are disabled"
        )

    tls_setting = os.getenv("FALKORDB_TLS")
    if tls_setting is None:
        use_tls = True
    else:
        normalized_tls = tls_setting.strip().lower()
        if normalized_tls not in {"1", "true", "yes", "0", "false", "no"}:
            raise ValueError("FALKORDB_TLS must be set to true or false")
        use_tls = normalized_tls in {"1", "true", "yes"}

    return {
        "username": os.getenv("FALKORDB_USERNAME", "").strip() or None,
        "ssl": use_tls,
        "ssl_cert_reqs": "required" if use_tls else None,
        "ssl_check_hostname": use_tls,
        "socket_connect_timeout": 10,
        "socket_timeout": 30,
    }
