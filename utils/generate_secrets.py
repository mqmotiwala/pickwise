"""
Generates .streamlit/secrets.toml from environment variables at startup.

Streamlit's native authentication (st.login / st.user) reads its config from
the [auth] section of .streamlit/secrets.toml -- it does NOT read environment
variables. 

Railway (deployment platform) only reads from env vars so this script materializes the file before the
app launches. It is invoked from the Railway start command.

Note: local development uses a real .streamlit/secrets.toml and never runs this.
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


def _require_all(names):
    """
    Resolve every required env var at once.

    Reports the full present/missing picture in one go rather than dying on the
    first gap, so a failed deploy tells you everything that needs setting
    instead of costing one deploy cycle per missing variable. Only names are
    printed, never values.
    """

    values, missing = {}, []
    for name in names:
        value = os.getenv(name)
        if value:
            values[name] = value
        else:
            missing.append(name)

    print(f"[generate_secrets] present: {sorted(values) or 'none'}")

    if missing:
        print(f"[generate_secrets] MISSING: {missing}")
        raise SystemExit(
            f"[generate_secrets] Missing required env var(s): {', '.join(missing)}. "
            "Set them on the Railway service (Variables tab); this script only copies "
            "them into .streamlit/secrets.toml, it does not invent values."
        )

    return values


def _toml_escape(value):
    """Escape a value for a TOML basic string (quotes and backslashes)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def main():
    values = {name: _toml_escape(v) for name, v in _require_all(REQUIRED_VARS).items()}

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

    # log the resolved absolute path and cwd, never secret values. Streamlit

    # looks for secrets.toml relative to where it is launched, so a mismatch

    # here is the thing to look at if the file is written but not picked up.

    print(f"[generate_secrets] cwd: {Path.cwd()}")

    print(f"[generate_secrets] Wrote {path.resolve()} with [auth] and [auth.google] sections")


if __name__ == "__main__":
    main()
