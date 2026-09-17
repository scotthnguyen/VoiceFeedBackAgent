"""Tiny web server: serves the feedback page and mints LiveKit access tokens.

The QR code points at this server (PUBLIC_URL). When a customer opens the page
and taps "Start", the browser asks /token for a short-lived JWT, then joins a
fresh LiveKit room. Because the agent worker runs with no explicit agent_name,
LiveKit auto-dispatches it into every new room, so it joins and starts talking.

Run:  python -m web.token         (serves on 0.0.0.0:8080)
"""

from __future__ import annotations

import io
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS

from agent import stores

load_dotenv()

_WEB_DIR = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=None)
CORS(app)


@app.get("/")
def index():
    return send_from_directory(_WEB_DIR, "index.html")


@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


@app.get("/logo.jpg")
def logo():
    """Presto wordmark, used as the caller avatar on the call screen."""
    return send_from_directory(_WEB_DIR.parent, "press_release_thumbnail.jpg")


@app.get("/stores")
def store_list():
    """The store registry, so the page can show a real (random) store on the sign."""
    return jsonify(
        [
            {
                "store_id": s.store_id,
                "brand": s.brand,
                "name": s.name,
                "city": s.city,
                "state": s.state,
            }
            for s in stores.all_stores()
        ]
    )


@app.get("/qr.png")
def qr_png():
    """QR encoding PUBLIC_URL (what a phone scans to reach this page)."""
    import qrcode

    url = os.environ.get("PUBLIC_URL", request.host_url.rstrip("/"))
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), mimetype="image/png")


@app.get("/token")
def token():
    """Return {url, room, identity, token} for a new feedback session."""
    from livekit import api

    lk_url = os.environ["LIVEKIT_URL"]
    api_key = os.environ["LIVEKIT_API_KEY"]
    api_secret = os.environ["LIVEKIT_API_SECRET"]

    room = request.args.get("room") or f"feedback-{uuid.uuid4().hex[:8]}"
    identity = request.args.get("identity") or f"customer-{uuid.uuid4().hex[:6]}"

    grant = api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
    jwt = (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name("Drive-thru customer")
        .with_grants(grant)
        .to_jwt()
    )
    return jsonify(url=lk_url, room=room, identity=identity, token=jwt)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
