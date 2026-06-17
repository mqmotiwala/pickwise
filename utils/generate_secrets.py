"""
Generates .streamlit/secrets.toml from environment variables at startup.

Streamlit's native authentication (st.login / st.user) reads its config from
the [auth] section of .streamlit/secrets.toml -- it does NOT read environment
variables. On hosts like Railway we only have env vars and no committed
secrets.toml (it's gitignored), so this script materializes the file before the
app launches. It is invoked from the Railway start command; local development
uses a real .streamlit/secrets.toml and never runs this.
"""

import os
from pathlib import Path

try:
    import tomllib  # stdlib (Python 3.11+); used only to validate the output
except ModuleNotFoundError:  # older interpreters: skip validation, still write the file
    tomllib = None

REQUIRED_VARS = (
    "REDIRECT_URI",
    "COOKIE_SECRET",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
)

GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"


def _require(name):
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"[generate_secrets] Missing required env var: {name}")
    return value


def _toml_escape(value):
    """Escape a value for a TOML basic string (quotes and backslashes)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def main():
    values = {name: _toml_escape(_require(name)) for name in REQUIRED_VARS}

    secrets = (
        "[auth]\n"
        f'redirect_uri = "{values["REDIRECT_URI"]}"\n'
        f'cookie_secret = "{values["COOKIE_SECRET"]}"\n'
        "\n"
        "[auth.google]\n"
        f'client_id = "{values["GOOGLE_CLIENT_ID"]}"\n'
        f'client_secret = "{values["GOOGLE_CLIENT_SECRET"]}"\n'
        f'server_metadata_url = "{GOOGLE_METADATA_URL}"\n'
    )

    # fail fast if interpolation produced invalid TOML
    if tomllib is not None:
        tomllib.loads(secrets)

    path = Path(".streamlit/secrets.toml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secrets, encoding="utf-8")

    # never print secret values
    print(f"[generate_secrets] Wrote {path} with [auth] and [auth.google] sections")


if __name__ == "__main__":
    main()
