"""
Tiny .env loader — no external dependency.

Import this at the top of any script (`import load_env`) and it will read a
`.env` file sitting next to it and copy any KEY=VALUE lines into os.environ
(without overwriting variables already set in the real environment).

Kept dependency-free on purpose: the spec pins us to requests + psycopg2 only.
"""

import os


def load_env(path=None):
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            # strip optional surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)


# Load on import so `import load_env` is all a script needs.
load_env()
