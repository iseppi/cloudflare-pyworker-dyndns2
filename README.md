# cloudflare-pyworker-dyndns2

A Cloudflare Worker written in Python that implements the [dyndns2 protocol](https://help.dyn.com/remote-access-api/), allowing standard DDNS clients to update DNS records hosted in Cloudflare.

See [SPEC.md](SPEC.md) for the full project specification.

## Features

- Full dyndns2 protocol compatibility (`/nic/update` and `/nic/checkip`)
- HTTP Basic Authentication with hashed passwords
- IPv4 (A record) and IPv6 (AAAA record) support
- Update-only — will not create new DNS records
- Multi-user support with per-user hostname authorisation
- Uses the official Cloudflare Python SDK

## Prerequisites

- [uv](https://docs.astral.sh/uv/#installation) — Python package manager
- [Node.js](https://nodejs.org/) — required by wrangler
- A Cloudflare account with DNS zones configured

## Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/iseppi/cloudflare-pyworker-dyndns2.git
   cd cloudflare-pyworker-dyndns2
   ```

2. Install dependencies:

   ```bash
   uv sync
   ```

3. Create a Cloudflare API token with **DNS Edit** permissions for your zones.

4. Generate your AUTH_JSON configuration:

   ```bash
   pip install argon2-cffi
   python tools/generate_auth_json.py
   ```

   The script will interactively prompt for usernames, passwords, and hostnames, then output the complete JSON structure.

5. Set the Worker secrets:

   ```bash
   uv run pywrangler secret put CF_API_TOKEN
   uv run pywrangler secret put AUTH_JSON
   ```

   For `AUTH_JSON`, provide a JSON string like:

   ```json
   {
     "alice": {
       "password": "<hashed_password>",
       "hostnames": ["home.example.com", "vpn.example.com"]
     }
   }
   ```

## Development

Run the Worker locally:

```bash
uv run pywrangler dev
```

## Deployment

Install packages, trim unused SDK modules, and deploy:

```bash
uv run pywrangler deploy --dry-run   # let pywrangler install packages
./scripts/trim_python_modules.sh      # trim unused Cloudflare SDK modules
npx wrangler deploy                   # deploy with trimmed bundle
```

The trim script removes unused Cloudflare SDK modules (we only need DNS and Zones), reducing the bundle from ~3.5 MB to ~2.5 MB gzipped — well under the free plan's 3 MB limit.

## Usage

### Check your IP

```bash
curl https://your-worker.your-subdomain.workers.dev/nic/checkip
```

### Update a DNS record

```bash
curl -u "alice:password" "https://your-worker.your-subdomain.workers.dev/nic/update?hostname=home.example.com&myip=203.0.113.1"
```

### Client configuration (ddclient)

```
protocol=dyndns2
server=your-worker.your-subdomain.workers.dev
ssl=yes
login=alice
password=your-password
home.example.com
```

## Acknowledgements

This project was co-developed with [Claude](https://claude.ai) by Anthropic.

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
