#!/usr/bin/env python3
"""
n2_config.py — shared loader for the N2 pipeline's per-user configuration.

A single human-edited file, ``n2_config_file.txt`` (sibling of this module),
holds every credential and connection setting that varies per user/deployment:
the RTPR API key, the IBKR account number, the IBKR Gateway host/port, and the
clerk TCP host/port. Every script in the N2 pipeline locates this module
relative to its own ``__file__`` (they all live one level under ``scripts/``, so
``Path(__file__).resolve().parent.parent / 'config' / 'n2_config.py'``) and calls
:func:`load_config`, so a fresh clone of the repo runs after editing only
``n2_config_file.txt`` — no source edits required.

File format (labelled, one value per entry)::

    # comments and blank lines are ignored
    RTPR_API_KEY:
    rtpr_xxxxxxxxxxxxxxx

    IBKR_GATEWAY_PORT:
    4001          # trailing comments after the value are stripped

A line whose stripped text ends with ':' is a LABEL; the next non-blank,
non-comment line is its VALUE. This is the same shape the legacy
``RTPR_API-Key.txt`` used (``Key:`` then the key on the next line), generalized
to multiple keys.
"""

from pathlib import Path

DEFAULT_CONFIG_FILENAME = 'n2_config_file.txt'


def default_config_path() -> Path:
    """Absolute path to the config file shipped beside this module."""
    return Path(__file__).resolve().parent / DEFAULT_CONFIG_FILENAME


def load_config(path=None) -> dict:
    """Parse the labelled config file into a ``{LABEL: value}`` dict.

    ``path`` defaults to ``n2_config_file.txt`` beside this module. A missing or
    unreadable file yields an empty dict (callers fall back to their own
    defaults) rather than raising — the pipeline must never fail to start on a
    config read. Blank lines and lines starting with '#' are ignored; a label is
    any line whose stripped text ends with ':', and its value is the next
    non-blank, non-comment line (with any trailing ' #...' inline comment
    stripped).
    """
    cfg_path = Path(path) if path is not None else default_config_path()
    result: dict = {}
    try:
        lines = cfg_path.read_text(encoding='utf-8').splitlines()
    except Exception:
        return result

    pending_label = None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        # Strip a trailing inline comment (" #..." or "\t#...") from labels and
        # values alike. A full-line comment was already skipped above, and none
        # of our credential/connection values contain a space-hash sequence.
        for sep in (' #', '\t#'):
            idx = line.find(sep)
            if idx != -1:
                line = line[:idx].rstrip()
        if not line:
            continue
        if pending_label is not None:
            result[pending_label] = line
            pending_label = None
            continue
        if line.endswith(':'):
            pending_label = line[:-1].strip()
        # else: a stray line that is neither a label nor a pending value — ignore
    return result


def get(cfg: dict, key: str, default=None):
    """Return ``cfg[key]`` when present and non-empty, else ``default``."""
    val = cfg.get(key)
    if val is None or val == '':
        return default
    return val


def get_int(cfg: dict, key: str, default=None):
    """Return ``int(cfg[key])`` when present and parseable, else ``default``."""
    val = get(cfg, key, None)
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default
