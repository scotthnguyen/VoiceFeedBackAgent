"""Generate the drive-thru feedback QR code.

The QR encodes the feedback web page URL (PUBLIC_URL). It's the *same* code for
every store — the agent asks which store you're at, so one sticker works fleet-wide.

Usage:
  python scripts/make_qr.py                      # uses PUBLIC_URL from .env
  python scripts/make_qr.py https://abc.ngrok.io # or pass a URL explicitly
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import qrcode
from dotenv import load_dotenv

load_dotenv()

OUT_PATH = Path(__file__).resolve().parent / "feedback_qr.png"


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PUBLIC_URL", "http://localhost:8080")

    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img.save(OUT_PATH)

    print(f"Encoded URL : {url}")
    print(f"Saved PNG   : {OUT_PATH}")
    print("\nScan preview (or open the PNG):\n")
    qr.print_ascii(invert=True)
    if "localhost" in url or "127.0.0.1" in url:
        print(
            "\nNote: a phone can't reach localhost. For an on-phone demo, expose the web "
            "server with a tunnel (e.g. `ngrok http 8080`) and rerun with that https URL."
        )


if __name__ == "__main__":
    main()
