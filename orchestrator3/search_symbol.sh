#!/bin/bash
# Usage: search_symbol.sh PATTERN [DIRECTORY]
# Finds files matching PATTERN in name or content, sorted oldest→newest (ls -lhtr style).

PATTERN="${1:?Usage: $0 PATTERN [DIRECTORY]}"
DIR="${2:-.}"

mapfile -t FILES < <(
    {
        find "$DIR" -type f -iname "*${PATTERN}*" 2>/dev/null
        rg -l "$PATTERN" "$DIR" 2>/dev/null
    } | sort -u | grep -v '^$'
)

[[ ${#FILES[@]} -eq 0 ]] && { echo "No matches for: $PATTERN"; exit 1; }

ls -lhtr "${FILES[@]}"
