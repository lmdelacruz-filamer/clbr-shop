"""Download third-party front-end files once and serve them from our own domain.

Why: no visitor IP/requests leak to Google or CDNs, no outage/compromise risk from a third-party host,
and the staff scanner page (customer details!) runs only code we host. Run automatically in the Docker build.
Each file is pinned to an exact version. Missing files simply make the templates fall back to the CDN.
"""
import re
import urllib.request
from pathlib import Path

from django.core.management.base import BaseCommand

DEST = Path(__file__).resolve().parents[2] / "static" / "store" / "vendor"

FILES = {
    "tailwind.js": "https://cdn.tailwindcss.com/3.4.17",
    "qrcode.min.js": "https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js",
    "html5-qrcode.min.js": "https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js",
}
FONTS_CSS = ("https://fonts.googleapis.com/css2?family=Pirata+One&family=Space+Mono:wght@400;700&display=swap")
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/124.0 Safari/537.36")          # Google serves woff2 files to modern browsers only


def fetch(url, ua="clbr-vendor-assets/1.0"):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def localise_font_css(css, download):
    """Keep only latin/latin-ext blocks, download each font file via download(url)->bytes,
    rewrite urls to fonts/<name>. Returns (new_css, {filename: bytes})."""
    out, files = [], {}
    for m in re.finditer(r"/\*\s*([\w-]+)\s*\*/\s*(@font-face\s*\{.*?\})", css, re.S):
        subset, block = m.group(1), m.group(2)
        if subset not in ("latin", "latin-ext"):
            continue
        def swap(u):
            url = u.group(1)
            name = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("/")[-1])
            if not name.endswith(".woff2"):
                name += ".woff2"
            files[name] = download(url)
            return f"url(fonts/{name})"
        out.append(re.sub(r"url\((https://[^)]+)\)", swap, block))
    return "\n".join(out) + "\n", files


class Command(BaseCommand):
    help = "Download Tailwind, QR libraries and fonts into static/ so they are served from our own server."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true", help="exit with an error if any download fails")

    def handle(self, *args, **opts):
        DEST.mkdir(parents=True, exist_ok=True)
        (DEST / "fonts").mkdir(exist_ok=True)
        failed = []
        for name, url in FILES.items():
            try:
                data = fetch(url)
                if len(data) < 1000:
                    raise ValueError("suspiciously small file")
                (DEST / name).write_bytes(data)
                self.stdout.write(f"  ok  {name} ({len(data) // 1024} KB)")
            except Exception as e:                                   # noqa: BLE001
                failed.append(name)
                self.stderr.write(f"  FAILED {name}: {e}")
        try:
            css, files = localise_font_css(fetch(FONTS_CSS, CHROME_UA).decode("utf-8"), lambda u: fetch(u, CHROME_UA))
            if not files:
                raise ValueError("no font files found in Google's CSS")
            for fname, data in files.items():
                (DEST / "fonts" / fname).write_bytes(data)
            (DEST / "fonts.css").write_text(css, encoding="utf-8")
            self.stdout.write(f"  ok  fonts.css + {len(files)} font file(s)")
        except Exception as e:                                       # noqa: BLE001
            failed.append("fonts")
            self.stderr.write(f"  FAILED fonts: {e}")
        if failed:
            msg = f"Could not download: {', '.join(failed)}. The site will use the public CDN for those."
            if opts["strict"]:
                raise SystemExit(msg)
            self.stderr.write(self.style.WARNING(msg))
        else:
            self.stdout.write(self.style.SUCCESS("All third-party assets are now self-hosted."))
