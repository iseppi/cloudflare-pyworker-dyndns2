#!/usr/bin/env python3
"""
AUTH_JSON generator for cloudflare-pyworker-dyndns2.

Interactively builds a complete AUTH_JSON configuration with Argon2id
password hashes, supporting multiple users and hostnames.

Usage:
    pip install argon2-cffi
    python tools/generate_auth_json.py
"""

import getpass
import json
import sys

try:
    from argon2 import PasswordHasher
except ImportError:
    print("Error: argon2-cffi is required. Install it with:")
    print("  pip install argon2-cffi")
    sys.exit(1)


def _prompt_user(ph):
    """Prompt for a single user's details. Returns (username, config_dict)."""
    username = input("  Username: ").strip()
    if not username:
        print("  Error: username cannot be empty.")
        return None, None

    password = getpass.getpass("  Password: ")
    if not password:
        print("  Error: password cannot be empty.")
        return None, None

    confirm = getpass.getpass("  Confirm password: ")
    if password != confirm:
        print("  Error: passwords do not match.")
        return None, None

    hostnames = []
    print("  Enter hostnames (one per line, empty line to finish):")
    while True:
        hostname = input("    Hostname: ").strip()
        if not hostname:
            break
        hostnames.append(hostname)

    if not hostnames:
        print("  Error: at least one hostname is required.")
        return None, None

    hash_value = ph.hash(password)

    return username, {
        "password": hash_value,
        "hostnames": hostnames,
    }


def main():
    ph = PasswordHasher()

    print("cloudflare-pyworker-dyndns2 — AUTH_JSON Generator")
    print("=" * 50)
    print()
    print("This tool will generate a complete AUTH_JSON configuration")
    print("with Argon2id password hashes for one or more users.")
    print()

    auth_json = {}

    while True:
        print(f"--- User {len(auth_json) + 1} ---")
        username, config = _prompt_user(ph)

        if username is None:
            print()
            retry = input("Try again? (y/n): ").strip().lower()
            if retry != "y":
                break
            print()
            continue

        if username in auth_json:
            print(f"  Warning: overwriting existing entry for '{username}'.")

        auth_json[username] = config
        print(f"  Added user '{username}' with {len(config['hostnames'])} hostname(s).")
        print()

        another = input("Add another user? (y/n): ").strip().lower()
        if another != "y":
            break
        print()

    if not auth_json:
        print()
        print("No users configured. Exiting.")
        sys.exit(0)

    output = json.dumps(auth_json, indent=2)

    print()
    print("=" * 50)
    print("AUTH_JSON configuration:")
    print("=" * 50)
    print()
    print(output)
    print()
    print("To set this as a Worker secret, run:")
    print()
    print("  uv run pywrangler secret put AUTH_JSON")
    print()
    print("Then paste the JSON above when prompted.")
    print()


if __name__ == "__main__":
    main()
