#!/bin/bash
# trim_python_modules.sh
#
# Removes unused Cloudflare SDK modules from python_modules/ to reduce
# the Worker bundle size. We only need the dns and zones resources.
#
# This script is run after pywrangler installs packages but before
# wrangler bundles the Worker for deployment.

set -e

MODULES_DIR="python_modules/cloudflare"

if [ ! -d "$MODULES_DIR" ]; then
    echo "No cloudflare SDK found in python_modules/, skipping trim."
    exit 0
fi

echo "Trimming unused Cloudflare SDK modules..."

# Keep track of size before
BEFORE=$(du -sk python_modules/ | cut -f1)

# Remove all resource modules except dns, zones, and the base files
cd "$MODULES_DIR/resources"
for dir in */; do
    dir="${dir%/}"
    case "$dir" in
        dns|zones|__pycache__)
            ;;
        *)
            rm -rf "$dir"
            ;;
    esac
done
# Remove top-level resource files except dns, zones, and __init__
for f in *.py; do
    case "$f" in
        __init__.py|dns.py|zones.py)
            ;;
        *)
            rm -f "$f"
            ;;
    esac
done
cd - > /dev/null

# Remove all type modules except dns, zones, shared, shared_params, and the base files
cd "$MODULES_DIR/types"
for dir in */; do
    dir="${dir%/}"
    case "$dir" in
        dns|zones|shared|shared_params|__pycache__)
            ;;
        *)
            rm -rf "$dir"
            ;;
    esac
done
# Remove top-level type files except dns, zones, shared, shared_params, and __init__
for f in *.py; do
    case "$f" in
        __init__.py|dns.py|zones.py|shared.py|shared_params.py)
            ;;
        *)
            rm -f "$f"
            ;;
    esac
done
cd - > /dev/null

# Remove __pycache__ directories
find python_modules/ -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

AFTER=$(du -sk python_modules/ | cut -f1)
echo "Trimmed python_modules/: ${BEFORE}K -> ${AFTER}K"
