#!/usr/bin/env python3
"""
Password hashing utility for cloudflare-pyworker-dyndns2 AUTH_JSON configuration.

Generates Argon2id password hashes suitable for pasting into the AUTH_JSON secret.

Usage:
    pip install argon2-cffi
    python tools/hash_password.py

The script will prompt for a password and output the hash.
"""

import getpass
import sys

try:
    from argon2 import PasswordHasher
except ImportError:
    print("Error: argon2-cffi is required. Install it with:")
    print("  pip install argon2-cffi")
    sys.exit(1)


def main():
    ph = PasswordHasher()

    print("cloudflare-pyworker-dyndns2 — Password Hash Generator")
    print("=" * 55)
    print()

    password = getpass.getpass("Enter password: ")
    if not password:
        print("Error: password cannot be empty.")
        sys.exit(1)

    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Error: passwords do not match.")
        sys.exit(1)

    hash_value = ph.hash(password)

    print()
    print("Argon2id hash (copy this into your AUTH_JSON):")
    print()
    print(f"  {hash_value}")
    print()
    print("Example AUTH_JSON entry:")
    print()
    print('  {')
    print('    "username": {')
    print(f'      "password": "{hash_value}",')
    print('      "hostnames": ["home.example.com"]')
    print("    }")
    print("  }")
    print()


if __name__ == "__main__":
    main()
