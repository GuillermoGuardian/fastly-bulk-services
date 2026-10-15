from __future__ import annotations

import ipaddress
import re

_LABEL = r"(?!-)[a-z0-9_-]{1,63}(?<!-)"
HOSTNAME_RE = re.compile(rf"^{_LABEL}(\.{_LABEL})*$")

_SECOND_LEVEL = {"com", "net", "org", "gov", "edu", "mil", "co", "ac"}


def is_valid_hostname(value: str) -> bool:
    return bool(value) and len(value) <= 253 and bool(HOSTNAME_RE.match(value))


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def is_valid_address(value: str) -> bool:
    return is_ip(value) or is_valid_hostname(value.lower())


def parse_port(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if not text.isdigit():
        return None
    port = int(text)
    return port if 1 <= port <= 65535 else None


def registrable_domain(host: str) -> str:
    """Heuristic eTLD+1 (no public suffix list): handles com.br / co.uk style suffixes."""
    labels = host.lower().rstrip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])
