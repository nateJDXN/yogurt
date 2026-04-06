#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_DEST="$HOME/scripts/granola-export.py"
PLIST_TEMPLATE="$REPO_DIR/local.granola-export.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/local.granola-export.plist"
LABEL="local.granola-export"
MIN_PYTHON="3.8"

echo "Installing Yogurt..."

# --- Ask for output directory ---
DEFAULT_OUTPUT_DIR="$HOME/Documents/granola-notes"

while true; do
    printf "\nWhere do you want your Granola notes to be saved?\n"
    printf "(leave blank for default: %s)\n> " "$DEFAULT_OUTPUT_DIR"
    read -r OUTPUT_DIR

    # Use default if blank
    if [[ -z "$OUTPUT_DIR" ]]; then
        OUTPUT_DIR="$DEFAULT_OUTPUT_DIR"
    fi

    # Expand ~ to $HOME
    OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"

    if [[ -d "$OUTPUT_DIR" ]]; then
        break
    fi

    echo ""
    echo "Directory does not exist: $OUTPUT_DIR"
    printf "Do you want to create it? (Y/N): "
    read -r CREATE_DIR

    if [[ "$CREATE_DIR" =~ ^[Yy]$ ]]; then
        mkdir -p "$OUTPUT_DIR"
        echo "Created: $OUTPUT_DIR"
        break
    fi
done

echo ""

# --- Preflight: find and verify python3 ---
PYTHON3="$(command -v python3 2>/dev/null || true)"
if [[ -z "$PYTHON3" ]]; then
    echo "ERROR: python3 not found on PATH." >&2
    echo "Install Python 3.8+ from https://www.python.org or via Homebrew: brew install python" >&2
    exit 1
fi
# Resolve symlinks to get the real path (stable across shell sessions / launchd)
PYTHON3="$(realpath "$PYTHON3")"
PY_VERSION="$("$PYTHON3" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$(printf '%s\n' "$MIN_PYTHON" "$PY_VERSION" | sort -V | head -n1)" != "$MIN_PYTHON" ]]; then
    echo "ERROR: Python $PY_VERSION found, but $MIN_PYTHON+ is required." >&2
    exit 1
fi
#echo "  Using $PYTHON3 ($PY_VERSION)"

# --- Copy the export script ---
mkdir -p "$(dirname "$SCRIPT_DEST")"
cp "$REPO_DIR/granola-export.py" "$SCRIPT_DEST"
chmod +x "$SCRIPT_DEST"
# Patch the default output directory in the installed script
sed -i '' "s|DEFAULT_OUTPUT_DIR = os.path.expanduser(\"~/Documents/granola-notes\")|DEFAULT_OUTPUT_DIR = \"$OUTPUT_DIR\"|" "$SCRIPT_DEST"
#echo "  Script installed to $SCRIPT_DEST"

# --- Generate plist with real paths ---
mkdir -p "$(dirname "$PLIST_DEST")"
sed -e "s|__HOME__|$HOME|g" -e "s|__PYTHON3__|$PYTHON3|g" -e "s|__OUTPUT_DIR__|$OUTPUT_DIR|g" "$PLIST_TEMPLATE" > "$PLIST_DEST"
#echo "  LaunchAgent installed to $PLIST_DEST"

# --- Load the agent (unload first if already loaded) ---
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
#echo "  LaunchAgent loaded"

# --- Offer initial export ---
CACHE_FILE="$HOME/Library/Application Support/Granola/cache-v6.json"
if [[ -f "$CACHE_FILE" ]]; then
    # Estimate export size: count documents and approximate ~4KB per note (header + panel/notes markdown)
    DOC_COUNT="$("$PYTHON3" -c "
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
docs = data.get('cache', {}).get('state', {}).get('documents', {})
print(len(docs))
" "$CACHE_FILE" 2>/dev/null || echo 0)"

    if [[ "$DOC_COUNT" -gt 0 ]]; then
        EST_KB=$((DOC_COUNT * 4))
        if [[ $EST_KB -ge 1024 ]]; then
            EST_SIZE="$(awk "BEGIN {printf \"%.1f\", $EST_KB / 1024}")MB"
        else
            EST_SIZE="${EST_KB}KB"
        fi

        printf "Found %d existing notes (~%s). Export them now? (Y/N): " "$DOC_COUNT" "$EST_SIZE"
        read -r EXPORT_NOW

        if [[ "$EXPORT_NOW" =~ ^[Yy]$ ]]; then
            echo ""
            "$PYTHON3" "$SCRIPT_DEST"
        else
            echo ""
            echo "No problem. To export later, run:"
            echo "  python3 $SCRIPT_DEST"
        fi
    fi
fi

echo ""
echo "Yogurt installed!"
echo ""
echo "To uninstall: ./uninstall.sh"
