from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException, status

# Cloud metadata endpoints -- these are the highest-value SSRF targets.
_BLOCKED_HOSTS = {
    "169.254.169.254",  # AWS / GCP / Azure metadata
    "metadata.google.internal",
}


def assert_safe_url(url: str) -> None:
    """Raises HTTPException(400) if `url` points at a private, loopback,
    link-local, or other non-public address. Call this AFTER building the
    final URL (with scheme prepended) and BEFORE handing it to whatever
    does the actual HTTP fetch.

    Resolves the hostname and checks the resolved IP, not just the
    hostname string -- this is what stops DNS rebinding (a public-looking
    hostname that resolves to 127.0.0.1 or an internal IP).

    Note: this only protects the INITIAL request. If your HTTP client
    follows redirects, it must re-validate on every redirect hop, or an
    attacker can pass a safe URL that 302s to an internal one. Prefer
    disabling redirect-following in the fetch client and doing the
    redirect loop manually with this check on each hop.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only http/https URLs are supported")

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid URL")

    if hostname.lower() in _BLOCKED_HOSTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This URL is not allowed")

    try:
        # getaddrinfo resolves the hostname the same way the actual fetch will --
        # checking this instead of the hostname string is what catches DNS rebinding.
        resolved = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not resolve URL")

    for family, _, _, _, sockaddr in resolved:
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This URL is not allowed")