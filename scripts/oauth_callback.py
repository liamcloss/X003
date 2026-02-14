"""Simple mkcert-backed HTTPS server that prints Threads OAuth callback params and exchanges the code for a token."""
from __future__ import annotations

import os
import ssl
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import httpx
from dotenv import load_dotenv

HOST = "localhost"
PORT = 8080
CERT_FILE = Path(__file__).resolve().parent.parent / "localhost+2.pem"
KEY_FILE = Path(__file__).resolve().parent.parent / "localhost+2-key.pem"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_threads_config() -> dict[str, str]:
    """Load Threads credentials from the repo .env file."""
    load_dotenv(ENV_FILE, override=False)
    return {
        "client_id": os.getenv("THREADS_APP_ID", "").strip(),
        "client_secret": os.getenv("THREADS_APP_SECRET", "").strip(),
        "redirect_uri": f"https://{HOST}:{PORT}/callback",
    }


CONFIG = load_threads_config()


def exchange_code(code: str) -> dict[str, Any] | None:
    if not code:
        print("No authorization code received; skipping exchange.")
        return None

    client_id = CONFIG["client_id"]
    client_secret = CONFIG["client_secret"]
    redirect_uri = CONFIG["redirect_uri"]

    if not all((client_id, client_secret)):
        print(
            "Missing Threads credentials. Please populate THREADS_APP_ID and THREADS_APP_SECRET in .env."
        )
        return None

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
        "grant_type": "authorization_code",
    }

    try:
        response = httpx.post(
            "https://graph.threads.net/oauth/access_token",
            data=payload,
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        print(f"Token exchange failed: {error}")
        if error.response is not None:
            print("Response body:", error.response.text)
        return None

    token_data = response.json()
    print("✨ Received token payload ✨")
    for key, value in token_data.items():
        print(f"  {key}: {value}")

    return token_data


def exchange_long_lived(short_token: str) -> dict[str, Any] | None:
    """Request a long-lived token based on the short-lived access token."""
    if not short_token:
        print("Short-lived token missing; cannot request long-lived token.")
        return None

    params = {
        "grant_type": "th_exchange_token",
        "client_secret": CONFIG["client_secret"],
        "access_token": short_token,
    }

    try:
        response = httpx.get(
            "https://graph.threads.net/access_token",
            params=params,
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        print(f"Long-lived token request failed: {error}")
        if error.response is not None:
            print("Response body:", error.response.text)
        return None

    data = response.json()
    print("✨ Received long-lived token payload ✨")
    for key, value in data.items():
        print(f"  {key}: {value}")

    return data


def persist_env(updates: Mapping[str, str]) -> None:
    if not updates:
        return

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    lines = ENV_FILE.read_text().splitlines()
    seen: set[str] = set()

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue

        key = line.split("=", 1)[0].strip()
        if key in updates:
            lines[index] = f"{key}={updates[key]}"
            seen.add(key)

    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")

    final_text = "\n".join(lines)
    if final_text and not final_text.endswith("\n"):
        final_text += "\n"

    ENV_FILE.write_text(final_text)
    print("Updated .env with new Threads token information.")


def build_env_updates(
    short_data: dict[str, Any] | None, long_data: dict[str, Any] | None
) -> dict[str, str]:
    if short_data is None:
        return {}

    updates: dict[str, str] = {}
    token_value = (
        long_data.get("access_token")
        if long_data and long_data.get("access_token")
        else short_data.get("access_token")
    )
    if token_value:
        updates["THREADS_ACCESS_TOKEN"] = str(token_value)

    expires = (
        long_data.get("expires_in")
        if long_data and long_data.get("expires_in")
        else short_data.get("expires_in")
    )
    if expires is not None:
        updates["THREADS_ACCESS_TOKEN_EXPIRES_IN"] = str(expires)

    updates["THREADS_TOKEN_OBTAINED_AT"] = datetime.now(timezone.utc).isoformat()
    return updates


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def _write_response(self, body: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self._write_response("<h1>Not found</h1>", status=404)
            return

        params = parse_qs(parsed.query)
        code = params.get("code", [""])[0]
        state = params.get("state", [""])[0]

        print("=" * 60)
        print("✨ Received Threads OAuth callback ✨")
        print(f"  code:  {code or '<none>'}")
        print(f"  state: {state or '<none>'}")
        print(f"  raw:   {params}")
        print("=" * 60)

        token_response = exchange_code(code)
        long_response = None

        if token_response:
            if token_response.get("access_token"):
                long_response = exchange_long_lived(token_response["access_token"])
            updates = build_env_updates(token_response, long_response)
            persist_env(updates)

        body = (
            "<h1>Callback received</h1>"
            "<p>The authorization code was captured and exchanged for a token.</p>"
        )
        if token_response is None:
            body = (
                "<h1>Callback received</h1>"
                "<p>There was an issue exchanging the code; check the console.</p>"
            )
        elif long_response is None:
            body = (
                "<h1>Callback received</h1>"
                "<p>Token exchange completed but long-lived token was unavailable.</p>"
            )

        self._write_response(body)


def main() -> None:
    if not CERT_FILE.exists() or not KEY_FILE.exists():
        raise SystemExit(
            "mkcert files missing. Run mkcert and place localhost+2.pem/key alongside scripts/ directory."
        )

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(CERT_FILE), keyfile=str(KEY_FILE))

    server = HTTPServer((HOST, PORT), OAuthCallbackHandler)
    server.socket = context.wrap_socket(server.socket, server_side=True)

    print(f"Listening for HTTPS callbacks at https://{HOST}:{PORT}/callback")
    print("Use the same URL as the redirect_uri in your Meta app settings and OAuth link.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down callback listener")
        server.server_close()


if __name__ == "__main__":
    main()
