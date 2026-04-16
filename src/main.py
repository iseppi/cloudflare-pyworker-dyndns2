"""
cloudflare-pyworker-dyndns2

A Cloudflare Worker implementing the dyndns2 protocol to update DNS records
in Cloudflare. See SPEC.md for the full specification.
"""

import json
import ipaddress
from base64 import b64decode
from urllib.parse import urlparse, parse_qs

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from cloudflare import AsyncCloudflare

from workers import WorkerEntrypoint, Response


# Argon2id password hasher instance
_ph = PasswordHasher()

# In-memory zone ID cache (per-invocation)
_zone_cache = {}


def _parse_basic_auth(request):
    """Extract username and password from HTTP Basic Auth header.

    Returns (username, password) or (None, None) if auth is missing/invalid.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return None, None

    try:
        decoded = b64decode(auth_header[6:]).decode("utf-8")
        username, password = decoded.split(":", 1)
        return username, password
    except Exception:
        return None, None


def _verify_password(stored_hash, password):
    """Verify a password against an Argon2id hash.

    Returns True if the password matches, False otherwise.
    """
    try:
        return _ph.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def _classify_ips(myip_value):
    """Parse a myip parameter value and classify addresses as IPv4 or IPv6.

    Returns (list_of_ipv4, list_of_ipv6) or (None, None) if parsing fails.
    """
    ipv4_addrs = []
    ipv6_addrs = []

    parts = [p.strip() for p in myip_value.split(",") if p.strip()]
    for part in parts:
        try:
            addr = ipaddress.ip_address(part)
            if isinstance(addr, ipaddress.IPv4Address):
                ipv4_addrs.append(str(addr))
            else:
                ipv6_addrs.append(str(addr))
        except ValueError:
            return None, None

    return ipv4_addrs, ipv6_addrs


def _is_valid_fqdn(hostname):
    """Basic validation that a hostname looks like an FQDN."""
    if not hostname or "." not in hostname:
        return False
    labels = hostname.split(".")
    for label in labels:
        if not label or len(label) > 63:
            return False
        if not all(c.isalnum() or c == "-" for c in label):
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
    return True


def _get_zone_candidates(fqdn):
    """Generate zone name candidates for an FQDN using the algorithm in SPEC.md 6.1.

    For 2 labels or fewer, try the full hostname directly.
    For 3+ labels, try removing the first label first (most common case),
    then the full hostname, then remaining suffixes left to right.
    """
    labels = fqdn.split(".")
    candidates = []

    if len(labels) <= 2:
        # e.g. "example.com" — just try it directly
        candidates.append(fqdn)
    else:
        # e.g. "home.example.com" — try "example.com" first
        without_first = ".".join(labels[1:])
        candidates.append(without_first)

        # Then try the full FQDN
        candidates.append(fqdn)

        # Then iterate remaining suffixes, skipping the one we already tried
        for i in range(2, len(labels) - 1):
            suffix = ".".join(labels[i:])
            if suffix != without_first:
                candidates.append(suffix)

    return candidates


async def _resolve_zone_id(client, fqdn):
    """Resolve an FQDN to a Cloudflare zone ID.

    Uses the candidate algorithm and caches results.
    Returns the zone_id or None if no zone is found.
    """
    # Check cache first
    if fqdn in _zone_cache:
        return _zone_cache[fqdn]

    candidates = _get_zone_candidates(fqdn)

    for candidate in candidates:
        # Check if we've already resolved this candidate
        if candidate in _zone_cache:
            zone_id = _zone_cache[candidate]
            _zone_cache[fqdn] = zone_id
            return zone_id

        try:
            zones = await client.zones.list(name=candidate)
            if zones.result and len(zones.result) > 0:
                zone_id = zones.result[0].id
                _zone_cache[candidate] = zone_id
                _zone_cache[fqdn] = zone_id
                return zone_id
        except Exception:
            continue

    return None


async def _update_hostname(client, hostname, ipv4_list, ipv6_list):
    """Update DNS records for a single hostname.

    Returns a dyndns2 status line string.
    """
    # Resolve zone
    zone_id = await _resolve_zone_id(client, hostname)
    if zone_id is None:
        return "nohost"

    updated_ip = None
    any_nochg = False
    any_updated = False
    any_record_found = False

    # Process IPv4 addresses (A records)
    for ip in ipv4_list:
        try:
            records = await client.dns.records.list(
                zone_id=zone_id, type="A", name=hostname
            )
            if records.result and len(records.result) > 0:
                any_record_found = True
                record = records.result[0]
                if record.content == ip:
                    any_nochg = True
                    if updated_ip is None:
                        updated_ip = ip
                else:
                    await client.dns.records.edit(
                        dns_record_id=record.id,
                        zone_id=zone_id,
                        content=ip,
                    )
                    any_updated = True
                    updated_ip = ip
        except Exception:
            return "911"

    # Process IPv6 addresses (AAAA records)
    for ip in ipv6_list:
        try:
            records = await client.dns.records.list(
                zone_id=zone_id, type="AAAA", name=hostname
            )
            if records.result and len(records.result) > 0:
                any_record_found = True
                record = records.result[0]
                if record.content == ip:
                    any_nochg = True
                    if updated_ip is None:
                        updated_ip = ip
                else:
                    await client.dns.records.edit(
                        dns_record_id=record.id,
                        zone_id=zone_id,
                        content=ip,
                    )
                    any_updated = True
                    updated_ip = ip
        except Exception:
            return "911"

    if not any_record_found:
        return "nohost"

    if any_updated:
        return f"good {updated_ip}"

    if any_nochg:
        return f"nochg {updated_ip}"

    return "nohost"


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlparse(request.url)
        path = url.path.rstrip("/")

        if path == "/nic/checkip":
            return await self._handle_checkip(request)
        elif path == "/nic/update":
            return await self._handle_update(request)
        else:
            return Response("Not found", status=404)

    async def _handle_checkip(self, request):
        """Handle /nic/checkip — return the caller's IP address."""
        client_ip = request.headers.get("CF-Connecting-IP", "")
        return Response(client_ip, headers={"Content-Type": "text/plain"})

    async def _handle_update(self, request):
        """Handle /nic/update — the dyndns2 update endpoint."""
        # Parse and validate auth
        username, password = _parse_basic_auth(request)
        if username is None:
            return Response("badauth", headers={"Content-Type": "text/plain"})

        # Load auth config
        try:
            auth_json = json.loads(self.env.AUTH_JSON)
        except Exception:
            return Response("911", headers={"Content-Type": "text/plain"})

        # Authenticate
        user_config = auth_json.get(username)
        if user_config is None:
            return Response("badauth", headers={"Content-Type": "text/plain"})

        if not _verify_password(user_config["password"], password):
            return Response("badauth", headers={"Content-Type": "text/plain"})

        # Parse query parameters
        url = urlparse(request.url)
        params = parse_qs(url.query)

        # Validate hostname parameter
        hostname_param = params.get("hostname", [None])[0]
        if not hostname_param:
            return Response("notfqdn", headers={"Content-Type": "text/plain"})

        hostnames = [h.strip() for h in hostname_param.split(",") if h.strip()]
        if not hostnames:
            return Response("notfqdn", headers={"Content-Type": "text/plain"})

        # Determine IP addresses to use
        myip_param = params.get("myip", [None])[0]

        if myip_param:
            ipv4_list, ipv6_list = _classify_ips(myip_param)
            if ipv4_list is None:
                return Response("911", headers={"Content-Type": "text/plain"})
        else:
            # Auto-detect from CF-Connecting-IP
            client_ip = request.headers.get("CF-Connecting-IP", "")
            if not client_ip:
                return Response("911", headers={"Content-Type": "text/plain"})
            ipv4_list, ipv6_list = _classify_ips(client_ip)
            if ipv4_list is None:
                return Response("911", headers={"Content-Type": "text/plain"})

        if not ipv4_list and not ipv6_list:
            return Response("911", headers={"Content-Type": "text/plain"})

        # Initialise Cloudflare API client
        try:
            client = AsyncCloudflare(api_token=self.env.CF_API_TOKEN)
        except Exception:
            return Response("911", headers={"Content-Type": "text/plain"})

        # Process each hostname
        allowed_hostnames = user_config.get("hostnames", [])
        results = []

        for hostname in hostnames:
            # Validate FQDN
            if not _is_valid_fqdn(hostname):
                results.append("notfqdn")
                continue

            # Authorise
            if hostname not in allowed_hostnames:
                results.append("nohost")
                continue

            # Update
            result = await _update_hostname(client, hostname, ipv4_list, ipv6_list)
            results.append(result)

        response_body = "\n".join(results)
        return Response(response_body, headers={"Content-Type": "text/plain"})
