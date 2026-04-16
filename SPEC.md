# Cloudflare Worker Dynamic DNS (dyndns2) — Project Specification

## 1. Overview

A Cloudflare Worker written in Python that implements the dyndns2 protocol, allowing standard DDNS clients (ddclient, inadyn, router built-in updaters, etc.) to update DNS records hosted in Cloudflare.

The Worker authenticates and authorises requests against a JSON configuration stored as a Worker secret, then translates valid update requests into Cloudflare DNS API calls.

## 2. Dependencies

### 2.1 Cloudflare Python SDK

The Worker uses the official `cloudflare` Python library (`cloudflare-python`) for all Cloudflare DNS API interactions. The async client (`AsyncCloudflare`) is used, since Worker handlers are async.

```python
from cloudflare import AsyncCloudflare

client = AsyncCloudflare(api_token=env.CF_API_TOKEN)
```

The library is a pure Python package built on `httpx` and `pydantic`, both of which are supported in the Cloudflare Workers Python runtime (Pyodide). It provides typed request/response objects and handles pagination, error responses, and retries.

**Fallback:** If the `cloudflare-python` library proves incompatible with the Workers Python runtime, the fallback is to make direct HTTP requests to the Cloudflare REST API using `httpx` (which is confirmed to work in Workers). The API endpoints and logic remain identical.

### 2.2 Standard Library

The following Python standard library modules are used:

- `ipaddress` — for detecting whether an address is IPv4 or IPv6
- `json` — for parsing the `AUTH_JSON` secret
- `base64` — for decoding HTTP Basic Auth credentials

## 3. Endpoints

### 3.1 `GET /nic/checkip`

Returns the caller's IP address as plain text, detected from the `CF-Connecting-IP` header.

**Response:**
- Content-Type: `text/plain`
- Body: the caller's IP address (IPv4 or IPv6 depending on how they connected)

### 3.2 `GET /nic/update`

The primary dyndns2 update endpoint.

**Query Parameters:**
- `hostname` (required) — one or more comma-separated FQDNs to update
- `myip` (optional) — one or more comma-separated IP addresses (IPv4 and/or IPv6). If omitted, the IP is auto-detected from `CF-Connecting-IP`.

**Authentication:**
- HTTP Basic Authentication
- Credentials validated against the `AUTH_JSON` secret

## 4. Authentication & Authorisation

### 4.1 Credential Storage

User credentials are stored in a Worker secret named `AUTH_JSON` as a JSON string with the following structure:

```json
{
  "alice": {
    "password": "$argon2id$v=19$m=65536,t=3,p=4$...",
    "hostnames": ["home.example.com", "vpn.example.com"]
  },
  "bob": {
    "password": "$argon2id$v=19$m=65536,t=3,p=4$...",
    "hostnames": ["office.example.net"]
  }
}
```

- Keys are usernames.
- `password` contains a salted hash produced by a suitable password hashing algorithm (see Section 9).
- `hostnames` is a list of FQDNs the user is authorised to update.

### 4.2 Authentication Flow

1. Extract the `Authorization` header and decode the Base64 Basic Auth credentials.
2. Look up the username in `AUTH_JSON`.
3. Verify the supplied password against the stored hash.
4. If authentication fails at any step, return `badauth`.

### 4.3 Authorisation Flow

1. Parse the `hostname` query parameter (comma-separated list of FQDNs).
2. For each hostname, check that it exists in the authenticated user's `hostnames` list.
3. If a hostname is not authorised, return `nohost` for that hostname.

## 5. IP Address Handling

### 5.1 Auto-Detection

If the `myip` parameter is omitted, the Worker reads the `CF-Connecting-IP` header to determine the caller's IP address. This will be either an IPv4 or IPv6 address depending on how the client connected. Only the corresponding record type (A or AAAA) will be updated.

### 5.2 Explicit IP

If `myip` is provided:

- A single IPv4 address → update the A record (if it exists)
- A single IPv6 address → update the AAAA record (if it exists)
- Comma-separated mix of IPv4 and IPv6 → update A for each IPv4 address and AAAA for each IPv6 address (if the respective records exist)

### 5.3 IP Version Detection

Use Python's `ipaddress` module to determine whether a given address is IPv4 or IPv6.

## 6. DNS Update Logic

### 6.1 Zone ID Resolution

The Worker derives the zone ID at runtime by querying the Cloudflare API. Since the API's zone list endpoint matches zone names (not arbitrary hostnames), the Worker walks through candidate zone names using the following algorithm:

**Algorithm:**

1. Count the labels in the FQDN.
2. If the FQDN has **two labels or fewer** (e.g., `example.com`), try the full hostname directly — stripping a label would leave a TLD, which is unlikely to be a valid zone.
3. If the FQDN has **three or more labels** (e.g., `home.example.com`), remove the first label and try that (e.g., `example.com`). This is the most common case — a single subdomain of a zone — and will usually succeed on the first call.
4. If no match, try the **full FQDN** (in case the hostname itself is a zone).
5. If still no match, iterate through remaining suffixes **from left to right**, skipping the candidate already tried in step 3.

**Examples:**

| FQDN | Attempts (in order) |
|------|---------------------|
| `example.com` | `example.com` |
| `home.example.com` | `example.com` → `home.example.com` |
| `home.example.co.uk` | `example.co.uk` → `home.example.co.uk` → `co.uk` |
| `vpn.office.example.co.uk` | `office.example.co.uk` → `vpn.office.example.co.uk` → `example.co.uk` → `co.uk` |

```python
zones = await client.zones.list(name=candidate)
zone_id = zones.result[0].id
```

Zone IDs should be cached in-memory for the lifetime of the Worker invocation to avoid redundant API calls when updating multiple hostnames in the same zone.

### 6.2 Record Lookup

For each hostname and IP version, the Worker queries for existing DNS records:

```python
a_records = await client.dns.records.list(zone_id=zone_id, type="A", name="home.example.com")
aaaa_records = await client.dns.records.list(zone_id=zone_id, type="AAAA", name="home.example.com")
```

### 6.3 Update Behaviour

- **Update only** — the Worker will only update records that already exist. It will not create new records.
- If an A record exists and an IPv4 address is provided, update the A record.
- If an AAAA record exists and an IPv6 address is provided, update the AAAA record.
- If no matching record exists for the provided IP version, return `nohost` for that hostname.
- If the existing record already contains the same IP address, return `nochg`.

### 6.4 Record Update

The Worker uses the SDK's `edit` method (HTTP `PATCH`) rather than `update` (HTTP `PUT`). This allows us to change only the `content` (IP address) while leaving all other fields — including TTL, proxied status, comments, and tags — unchanged.

```python
await client.dns.records.edit(
    dns_record_id=record_id,
    zone_id=zone_id,
    content="203.0.113.1",
)
```

### 6.5 Preserved Fields

By using `PATCH`, the following fields are left unchanged on the existing record:

- **TTL**
- **Proxied status**
- **Comments**
- **Tags**

## 7. Response Format

All responses use `Content-Type: text/plain`.

The dyndns2 protocol returns one status line per hostname in the request.

### 7.1 Status Codes

| Code       | Meaning                                                    |
|------------|------------------------------------------------------------|
| `good <ip>`   | DNS record updated successfully to the given IP address  |
| `nochg <ip>`  | IP address is unchanged; no update performed             |
| `nohost`      | Hostname not found, not authorised, or no matching record exists |
| `badauth`     | Authentication failed                                    |
| `notfqdn`     | The supplied hostname is not a valid FQDN                |
| `abuse`       | Reserved for future use                                  |
| `911`         | Server-side error (Cloudflare API failure, etc.)         |

### 7.2 Multi-Hostname Responses

When multiple hostnames are provided in a single request, the response contains one status line per hostname, separated by newlines, **in the same order as the hostnames were specified in the request**. Example:

Request: `hostname=host1.example.com,host2.example.com,host3.example.com`

```
good 203.0.113.1
nochg 203.0.113.1
nohost
```

### 7.3 Dual-Stack Responses

When both an A and AAAA record are updated for a single hostname (via comma-separated `myip`), the Worker returns one status line per hostname (not per record update). The response should reflect the overall outcome:

- If both updates succeed: `good <ipv4>` (report the IPv4 address, consistent with convention)
- If one succeeds and one has no matching record: `good <successful_ip>`
- If both are `nochg`: `nochg <ipv4>`

## 8. Secrets

| Secret Name    | Purpose                                              |
|----------------|------------------------------------------------------|
| `AUTH_JSON`    | JSON string containing the user/password/hostname configuration |
| `CF_API_TOKEN` | Cloudflare API token with DNS edit permissions scoped to the relevant zones |

## 9. Password Hashing

Passwords in `AUTH_JSON` are stored as Argon2id hashes, using the `argon2-cffi` library. This library is supported in the Cloudflare Workers Python runtime (Pyodide includes `argon2-cffi-bindings`).

The Worker uses `argon2.PasswordHasher` for verification:

```python
from argon2 import PasswordHasher
ph = PasswordHasher()
ph.verify(stored_hash, password)
```

A standalone utility script (`tools/generate_auth_json.py`) is provided to interactively generate a complete `AUTH_JSON` configuration. It prompts for one or more users, including username, password (with confirmation), and hostnames, then outputs the full JSON structure with Argon2id hashes ready to paste as a Worker secret. It requires `argon2-cffi` to be installed locally:

```bash
pip install argon2-cffi
python tools/generate_auth_json.py
```

## 10. Error Handling

- Invalid or missing `Authorization` header → `badauth`
- Username not found → `badauth`
- Password mismatch → `badauth`
- Missing `hostname` parameter → `notfqdn`
- Hostname not a valid FQDN → `notfqdn`
- Hostname not in user's authorised list → `nohost`
- No existing DNS record for the hostname/IP version → `nohost`
- Cloudflare API errors → `911`
- Malformed `myip` value (not a valid IP address) → `911`

## 11. Rate Limiting

Relies on Cloudflare's built-in rate limiting and abuse protection. No application-level rate limiting is implemented.

## 12. Security Considerations

- All credentials are transmitted over HTTPS (enforced by Cloudflare Workers).
- Passwords are never stored in plaintext.
- The `CF_API_TOKEN` should be scoped to the minimum required permissions (DNS edit on specific zones).
- The `AUTH_JSON` secret is not exposed to clients.
- The Worker does not create new DNS records, reducing the attack surface for abuse.

## 13. Version Control

All project source code, configuration templates, documentation, and utility scripts are stored in a Git repository hosted on GitHub as a public repository.

The repository should include:

- Worker source code
- `wrangler.toml` / `pyproject.toml` configuration
- This specification document (`SPEC.md` in the repository root)
- Password hashing utility script
- A `.gitignore` appropriate for Python and Cloudflare Workers projects
- A `README.md` with setup and deployment instructions

Secrets (`AUTH_JSON`, `CF_API_TOKEN`) must never be committed to the repository. They are managed exclusively through Cloudflare Worker secrets.

## 14. Project Structure

```
cloudflare-pyworker-dyndns2/
├── src/
│   └── main.py              # Worker entry point
├── tools/
│   └── generate_auth_json.py  # AUTH_JSON configuration generator
├── pyproject.toml            # Python dependencies & project metadata
├── wrangler.toml             # Cloudflare Worker configuration
├── SPEC.md                   # Project specification
├── README.md                 # Setup & deployment instructions
└── .gitignore                # Git ignore rules
```

### 14.1 Tooling

The project uses the following tooling:

- **uv** — Python package manager
- **pywrangler** — Cloudflare's CLI for Python Workers (wraps wrangler)
- **Node.js** — required by wrangler

Local development:

```bash
uv run pywrangler dev
```

Deployment:

```bash
uv run pywrangler deploy
```

### 14.2 Configuration Files

**`wrangler.toml`** — Cloudflare Worker configuration:

- Worker name
- Entry point (`src/main.py`)
- Compatibility date and `python_workers` compatibility flag
- Secret bindings for `AUTH_JSON` and `CF_API_TOKEN`

**`pyproject.toml`** — Python project configuration:

- Project metadata (name, version, description)
- Runtime dependency: `cloudflare` (the official Python SDK)
- Dev dependencies: `workers-py`, `workers-runtime-sdk`

## 15. Future Considerations

These items are explicitly out of scope for the initial implementation but may be added later:

- Additional query parameters (e.g., `myipv6`, `myip6`) for extended dual-stack support
- Configurable TTL and proxied status per hostname
- Record creation (not just update)
- Logging and audit trail via Workers Analytics or KV
- Application-level rate limiting
- Wildcard and MX record support

## 16. Client Compatibility

The Worker is designed to be compatible with any dyndns2-compliant client, including:

- ddclient
- inadyn
- Fritz!Box and other router built-in DDNS clients
- curl / wget (manual updates)

Example curl update:

```bash
curl -u "alice:password" "https://ddns.example.com/nic/update?hostname=home.example.com&myip=203.0.113.1"
```

Example curl checkip:

```bash
curl "https://ddns.example.com/nic/checkip"
```
