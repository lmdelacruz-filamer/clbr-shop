"""CLBR Radio helpers: validate the SoundCloud link and parse the tracklist staff type in ops."""
import re
from urllib.parse import urlparse

_HOSTS = {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}
_SEG = re.compile(r"^[A-Za-z0-9_.\-]+$")
# Top-level SoundCloud pages that are not a user's track or set.
_RESERVED = {"discover", "stream", "you", "upload", "search", "charts", "pages", "terms-of-use", "feed", "messages", "settings", "tags", "popular"}
_LINE = re.compile(r"^\s*(\d{1,2}(?::\d{2}){1,2})\s*\|\s*(.+?)\s*\|\s*(.+?)\s*$")

MAX_TRACKS = 100


def clean_soundcloud_url(raw):
    """Return the bare https://soundcloud.com/<user>/<track> URL (tracking parameters removed) or raise ValueError."""
    raw = (raw or "").strip()
    p = urlparse(raw)
    if p.scheme != "https" or (p.hostname or "").lower() not in _HOSTS:
        raise ValueError("Paste the full https://soundcloud.com/... link of the mix.")
    if p.port or p.username or p.password:
        raise ValueError("That link has extra parts we cannot use. Copy it again from SoundCloud (Share, then Copy link).")
    segs = [s for s in p.path.split("/") if s]
    if len(segs) < 2 or segs[0].lower() in _RESERVED or not all(_SEG.match(s) for s in segs):
        raise ValueError("That looks like a profile or page, not a mix. Open the mix itself and copy its link.")
    return "https://soundcloud.com/" + "/".join(segs)


def to_seconds(stamp):
    parts = [int(x) for x in stamp.split(":")]
    if len(parts) == 2:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def label(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def parse_tracklist(text):
    """One line per track:  m:ss | Artist | Title   (h:mm:ss also works).
    Returns (tracks, errors). tracks = [{"start": seconds, "artist": ..., "title": ...}] sorted by start."""
    tracks, errors = [], []
    for n, line in enumerate((text or "").splitlines(), 1):
        if not line.strip():
            continue
        m = _LINE.match(line)
        if not m:
            errors.append(f"Line {n}: use  time | artist | title   for example  12:04 | Artist | Track name")
            continue
        stamp, artist, title = m.groups()
        parts = stamp.split(":")
        if any(int(x) > 59 for x in parts[1:]):
            errors.append(f"Line {n}: minutes and seconds must be under 60.")
            continue
        if len(artist) > 100 or len(title) > 120:
            errors.append(f"Line {n}: artist or title is too long.")
            continue
        start = to_seconds(stamp)
        tracks.append({"start": start, "label": label(start), "artist": artist, "title": title})
    if len(tracks) > MAX_TRACKS:
        errors.append(f"At most {MAX_TRACKS} tracks.")
    tracks.sort(key=lambda t: t["start"])
    return tracks, errors
