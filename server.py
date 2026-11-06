"""Serves V-Z-ble and proxies the AI review to Claude, so the API key stays on the server.

Run:  ANTHROPIC_API_KEY=... .venv/bin/python server.py

Locally without a key, review requests are queued in reviews/ for a Claude Code agent to answer:
each <id>.request.json holds the prompt and output schema; the agent writes <id>.result.json.

Settings (environment variables):
  HOSTED=1           public deployment: listen on all interfaces, no agent queue
  PORT               port to listen on (default 8765; Render sets it)
  DATA_DIR           where LinkedIn imports are stored (default ./data, or /tmp/v-z-ble when hosted)
  PUBLIC_URL         the site's public address, shown on share cards
  REVIEWS_PER_DAY    AI reviews per visitor per day when hosted (default 3)
  ANTHROPIC_API_KEY  enables the AI review
"""

import datetime
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Literal
from urllib.parse import parse_qs, urlparse

import anthropic
from pydantic import BaseModel

PORT = int(os.environ.get("PORT", "8765"))
HOSTED = os.environ.get("HOSTED") == "1"
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
REVIEWS_PER_DAY = int(os.environ.get("REVIEWS_PER_DAY", "3"))
MODEL = "claude-opus-5-5"
MAX_FIELD = 20_000  # characters per input field; keeps a single request bounded
MAX_BODY = 300_000  # bytes per request body
ROOT = Path(__file__).resolve().parent

SYSTEM = """You review software engineers' public profiles the way an experienced engineering \
hiring manager and developer-relations lead would. Be direct and specific; quote the exact \
words that weaken the text. Judge each piece against the person's stated goals; when there \
are several, favor rewrites that serve all of them.

Rules for rewrites:
- Keep them truthful. Use only facts present in the input. Where a number or detail would \
help but isn't given, insert a bracketed placeholder like [% latency cut] or [# users] \
for the person to fill in. Never invent employers, metrics, projects or technologies.
- Match each platform: LinkedIn headlines fit in 220 characters; About sections read in the \
first person; README rewrites are Markdown; post titles name a concrete problem or result; \
experience bullets start with a strong verb, say what you built or changed, and end with a \
measurable result, in one line each.
- Only review the sections that were provided. Skip a section entirely if its input is empty."""

GOALS = {
    "job": "landing a new software engineering role",
    "oss": "building open source credibility",
    "thought": "becoming known for thought leadership and speaking",
}


class Rewrite(BaseModel):
    original: str
    suggestion: str
    why: str


class Section(BaseModel):
    section: Literal["headline", "about", "experience", "readme", "post_titles"]
    score: int  # 1-10
    verdict: str
    strengths: List[str]
    problems: List[str]
    rewrites: List[Rewrite]


class Review(BaseModel):
    summary: str
    sections: List[Section]


client = anthropic.Anthropic() if os.environ.get("ANTHROPIC_API_KEY") else None
QUEUE = ROOT / "reviews"
ID_RE = re.compile(r"^[0-9]{13}$")
# Hosted (Cloud Run) has no disk: imports sit in a temporary folder only until the page copies them.
DATA_DIR = Path(os.environ.get("DATA_DIR") or ("/tmp/v-z-ble" if HOSTED else ROOT / "data"))
LEGACY_LINKEDIN = DATA_DIR / "linkedin.json"  # single-user import from before visitor IDs
USER_RE = re.compile(r"^[a-f0-9]{32}$")  # visitor ID: random, created by the page, sent as X-VZB-User
LINKEDIN_FIELDS = {
    "url": str, "name": str, "headline": str, "about": str, "experience": str, "skills": str,
    "education": str, "raw": str, "hasPhoto": bool, "hasFeatured": bool, "looksLikeOwnProfile": bool,
    "hasActivitySection": bool, "extensionVersion": str,
}
EXTENSION_MANIFEST = ROOT / "extension" / "manifest.json"
EXTENSION_ZIP_PATH = "/v-z-ble-extension.zip"


def extension_zip() -> bytes:
    """The Chrome extension as a download. It unzips into one folder, ready for Load unpacked."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(EXTENSION_MANIFEST.parent.rglob("*")):
            if path.suffix in (".js", ".json", ".png"):
                z.write(path, f"v-z-ble-extension/{path.relative_to(EXTENSION_MANIFEST.parent)}")
    return out.getvalue()


def linkedin_file(user: str) -> Path:
    """Each visitor's import lives in its own file. Locally, the first visitor inherits the
    import saved before visitor IDs existed."""
    path = DATA_DIR / "linkedin" / f"{user}.json"
    if not HOSTED and not path.exists() and LEGACY_LINKEDIN.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        LEGACY_LINKEDIN.rename(path)
    return path
LINKEDIN_COUNTS = ("recommendationsReceived", "lastPostDays", "skillsCount", "cutOffCount")  # whole numbers, or null if unknown


def save_linkedin(data: dict, user: str) -> tuple[int, dict]:
    """Stores the profile the browser extension read from LinkedIn (only the latest one per visitor)."""
    if not isinstance(data, dict) or not str(data.get("url", "")).startswith("https://www.linkedin.com/in/"):
        return 400, {"error": "Expected a LinkedIn profile from the browser extension."}
    profile = {k: t(data[k]) if t is bool else str(data[k])[:MAX_FIELD * 2 if k == "raw" else MAX_FIELD]
               for k, t in LINKEDIN_FIELDS.items() if data.get(k) is not None}
    for key, limit in (("sectionsFound", 40), ("imageHints", 8)):
        items = data.get(key)
        profile[key] = [str(x)[:120] for x in items[:limit]] if isinstance(items, list) else None
    for key in LINKEDIN_COUNTS:
        value = data.get(key)
        profile[key] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    # LinkedIn photo links expire, so fingerprint the photo now and keep only the fingerprint.
    photo_url = str(data.get("photoUrl") or "")
    profile["photoHash"] = photo_hash(photo_url)[1].get("hash") if photo_url else None
    profile["importedAt"] = int(time.time() * 1000)
    path = linkedin_file(user)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2))
    return 200, {"ok": True, "importedAt": profile["importedAt"]}


MEDIUM_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,60}$")


def fetch_medium(raw_user: str) -> tuple[int, dict]:
    """Reads a Medium author's RSS feed. Browsers can't (Medium sends no CORS headers), so the
    server does. Medium's feed only lists the latest 10 posts and has no clap counts."""
    user = re.sub(r"^(https?://)?(www\.)?medium\.com/", "", raw_user.strip()).lstrip("@").strip("/")
    if not MEDIUM_USER_RE.match(user):
        return 400, {"error": "Enter a Medium username like @jane or a medium.com/@jane link."}
    status, body = read_feed(f"https://medium.com/feed/@{user}", "Medium", f"Medium user @{user} not found.")
    if status != 200:
        return status, body
    # The channel title reads "Stories by Jane Doe on Medium"; its image is the author's photo.
    name = re.sub(r"^Stories by (.+) on Medium$", r"\1", body["channelTitle"])
    return 200, {"user": user, "profileUrl": f"https://medium.com/@{user}", "posts": body["posts"],
                 "name": name, "photo": body["channelImage"]}


SUBSTACK_HOST_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


def fetch_substack(raw: str) -> tuple[int, dict]:
    """Reads a Substack publication's feed: 'jane', 'jane.substack.com' or a custom domain."""
    host = re.sub(r"^https?://", "", raw.strip().lower()).split("/")[0]
    if host and "." not in host:
        host += ".substack.com"
    if not SUBSTACK_HOST_RE.match(host):
        return 400, {"error": "Enter your Substack like jane.substack.com or your custom domain."}
    status, body = read_feed(f"https://{host}/feed", "Substack", f"No Substack found at {host}.")
    if status != 200:
        return status, body
    # A Substack's image is the publication logo, not the author's photo, so only the name is used.
    return 200, {"user": host, "profileUrl": f"https://{host}", "posts": body["posts"],
                 "name": body["author"], "photo": ""}


def read_feed(url: str, source: str, not_found: str) -> tuple[int, object]:
    """Fetches an RSS feed and returns its posts (title, link, published ms, tags)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 V-Z-ble"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            root = ET.fromstring(r.read())
    except urllib.error.HTTPError as e:
        return (404, {"error": not_found}) if e.code == 404 else (502, {"error": f"{source} returned {e.code}."})
    except (urllib.error.URLError, TimeoutError, ET.ParseError):
        return 502, {"error": f"Couldn't read the {source} feed. Check the address, or try again in a moment."}

    posts = []
    for item in root.iter("item"):
        try:
            published = int(parsedate_to_datetime(item.findtext("pubDate", "")).timestamp() * 1000)
        except (TypeError, ValueError):
            continue
        posts.append({
            "title": (item.findtext("title") or "").strip(),
            "link": (item.findtext("link") or "").split("?")[0],
            "published": published,
            "tags": [c.text for c in item.findall("category") if c.text],
        })
    channel = root.find("channel")
    creators = [c.text.strip() for c in root.iter("{http://purl.org/dc/elements/1.1/}creator") if c.text]
    return 200, {
        "posts": posts,
        "channelTitle": (channel.findtext("title") if channel is not None else "") or "",
        "channelImage": (channel.findtext("image/url") if channel is not None else "") or "",
        # The most frequent post author, for feeds (like Substack) that name one per post.
        "author": max(set(creators), key=creators.count) if creators else "",
    }


LEETCODE_USER_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
LEETCODE_QUERY = """query($u: String!) {
  matchedUser(username: $u) {
    username
    profile { realName userAvatar }
    submitStatsGlobal { acSubmissionNum { difficulty count } }
    userCalendar { submissionCalendar }
  }
  userContestRanking(username: $u) { rating attendedContestsCount }
}"""


def fetch_leetcode(raw: str) -> tuple[int, dict]:
    """Reads a public LeetCode profile (solved counts, recent practice, contest rating) from the
    GraphQL endpoint leetcode.com's own profile page uses. It needs no sign-in, but browsers can't
    call it (no CORS headers), so the server does."""
    user = re.sub(r"^(https?://)?(www\.)?leetcode\.com/(u/)?", "", raw.strip(), flags=re.I).lstrip("@").split("/")[0]
    if not LEETCODE_USER_RE.match(user):
        return 400, {"error": "Enter a LeetCode username or a leetcode.com/u/you link."}
    req = urllib.request.Request(
        "https://leetcode.com/graphql",
        data=json.dumps({"query": LEETCODE_QUERY, "variables": {"u": user}}).encode(),
        headers={"Content-Type": "application/json", "Referer": "https://leetcode.com", "User-Agent": "Mozilla/5.0 V-Z-ble"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read()).get("data") or {}
    except urllib.error.HTTPError as e:
        return 502, {"error": f"LeetCode returned {e.code}."}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return 502, {"error": "Couldn't reach LeetCode. Try again in a moment."}
    found = data.get("matchedUser")
    if not found:
        return 404, {"error": f"LeetCode user {user} not found."}

    solved = {s["difficulty"].lower(): s["count"] for s in found["submitStatsGlobal"]["acSubmissionNum"]}
    # The calendar maps each day (unix seconds, UTC midnight) to that day's submission count.
    try:
        days = [int(t) for t in json.loads(found["userCalendar"]["submissionCalendar"] or "{}")]
    except (TypeError, ValueError):
        days = []
    now = time.time()
    contest = data.get("userContestRanking") or {}
    avatar = found["profile"].get("userAvatar") or ""
    return 200, {
        "user": found["username"], "profileUrl": f"https://leetcode.com/u/{found['username']}/",
        "name": found["profile"].get("realName") or "",
        "photo": "" if "default_avatar" in avatar else avatar,  # the stock avatar isn't the user's photo
        "solved": {k: solved.get(k, 0) for k in ("all", "easy", "medium", "hard")},
        "activeDays90": sum(1 for t in days if now - t <= 90 * 86400),
        "lastActiveDays": int((now - max(days)) // 86400) if days else None,
        "contestRating": round(contest["rating"]) if contest.get("attendedContestsCount") else None,
        "contests": contest.get("attendedContestsCount") or 0,
    }


PHOTO_HOSTS = (  # where platforms serve profile photos; nothing else is fetched
    "githubusercontent.com", "gravatar.com", "stack.imgur.com", "sstatic.net", "googleusercontent.com",
    "medium.com", "dev.to", "dev-to-uploads.s3.amazonaws.com", "licdn.com", "leetcode.com",
)
_photo_hashes: dict[str, str] = {}
MAX_CACHED_PHOTOS = 5000


def photo_hash(url: str) -> tuple[int, dict]:
    """Fingerprints a profile photo as a 64-bit difference hash (dHash): shrink to 9x8 greyscale,
    record whether each pixel is brighter than its right neighbour. The same photo, resized or
    recompressed by different sites, gives hashes only a few bits apart."""
    def allowed(u: str) -> bool:
        p = urlparse(u)
        return p.scheme == "https" and any(p.hostname == h or (p.hostname or "").endswith("." + h) for h in PHOTO_HOSTS)

    if not allowed(url):
        return 400, {"error": "Only profile photos from known platforms can be compared."}
    if url in _photo_hashes:
        return 200, {"hash": _photo_hashes[url]}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 V-Z-ble"})
        with urllib.request.urlopen(req, timeout=15) as r:
            if not allowed(r.geturl()):
                return 400, {"error": "The photo redirected to an unknown host."}
            data = r.read(5_000_000)
        from PIL import Image  # imported lazily so the rest of the server runs without Pillow
        img = Image.open(io.BytesIO(data)).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return 502, {"error": f"Couldn't read that photo ({type(e).__name__})."}
    px = list(img.getdata())
    bits = "".join("1" if px[row * 9 + col] > px[row * 9 + col + 1] else "0" for row in range(8) for col in range(8))
    if len(_photo_hashes) >= MAX_CACHED_PHOTOS:
        _photo_hashes.clear()
    _photo_hashes[url] = f"{int(bits, 2):016x}"
    return 200, {"hash": _photo_hashes[url]}


def load_linkedin(user: str) -> tuple[int, dict]:
    path = linkedin_file(user)
    if not path.exists():
        return 404, {"error": "No LinkedIn profile imported yet."}
    profile = json.loads(path.read_text())
    # The version on disk; if the import came from an older one, Chrome hasn't reloaded the extension.
    profile["latestExtensionVersion"] = json.loads(EXTENSION_MANIFEST.read_text()).get("version")
    return 200, profile


def build_prompt(data: dict) -> str:
    def field(key):
        return str(data.get(key) or "").strip()[:MAX_FIELD]

    # "goals" is a list from the page; older requests sent a single "goal".
    keys = data.get("goals") if isinstance(data.get("goals"), list) else [data.get("goal")]
    goals = [GOALS[k] for k in keys if k in GOALS] or [GOALS["job"]]
    parts = [("Goal: " if len(goals) == 1 else "Goals: ") + "; ".join(goals)]
    sections = [
        ("headline", "LinkedIn headline", field("headline")),
        ("about", "LinkedIn About section", field("about")),
        ("experience", "LinkedIn Experience section (roles and their bullets)", field("experience")),
        ("readme", "GitHub profile README (Markdown)", field("readme")),
        ("post_titles", "Recent blog post titles, one per line", field("titles")),
    ]
    provided = [s for s in sections if s[2]]
    for key, label, text in provided:
        parts.append(f'<{key} label="{label}">\n{text}\n</{key}>')
    if field("context"):
        parts.append(f"<context>\nOther facts about this person (use for rewrites):\n{field('context')}\n</context>")
    parts.append(
        "Review these sections: " + ", ".join(k for k, _, _ in provided) + ". "
        "For each: a 1-10 score, a one-sentence verdict, up to 3 strengths, up to 4 problems, "
        "and 1-3 rewrites. For the headline give 3 alternative headlines; for post titles rewrite "
        "the weakest 3 titles; for experience rewrite the weakest 3-5 bullets or role descriptions, "
        "quoting each original exactly. Then a 2-3 sentence overall summary of how these read together."
    )
    return "\n\n".join(parts)


def enqueue(data: dict) -> tuple[int, dict]:
    QUEUE.mkdir(exist_ok=True)
    review_id = str(int(time.time() * 1000))
    request = {
        "id": review_id,
        "system": SYSTEM,
        "prompt": build_prompt(data),
        "output_schema": Review.model_json_schema(),
        "write_result_to": str(QUEUE / f"{review_id}.result.json"),
    }
    (QUEUE / f"{review_id}.request.json").write_text(json.dumps(request, indent=2))
    return 202, {"id": review_id, "queued": True}


def queued_result(review_id: str) -> tuple[int, dict]:
    if not ID_RE.match(review_id) or not (QUEUE / f"{review_id}.request.json").exists():
        return 404, {"error": "No such review."}
    result = QUEUE / f"{review_id}.result.json"
    if not result.exists():
        return 202, {"id": review_id, "queued": True}
    try:
        return 200, Review.model_validate_json(result.read_text()).model_dump()
    except ValueError as e:
        return 502, {"error": f"The agent's review didn't match the expected format: {e}"}


_review_counts: dict[str, int] = {}  # "date|key" -> reviews used; resets on restart
_review_lock = threading.Lock()


def take_review_slot(keys: list[str]) -> bool:
    """Counts a review against each key (visitor ID and IP) for today; False if any is used up."""
    today = datetime.date.today().isoformat()
    with _review_lock:
        for stale in [k for k in _review_counts if not k.startswith(today)]:
            del _review_counts[stale]
        slots = [f"{today}|{k}" for k in keys if k]
        if any(_review_counts.get(s, 0) >= REVIEWS_PER_DAY for s in slots):
            return False
        for s in slots:
            _review_counts[s] = _review_counts.get(s, 0) + 1
        return True


def review(data: dict, user: str, ip: str = "") -> tuple[int, dict]:
    if not any(str(data.get(k) or "").strip() for k in ("headline", "about", "experience", "readme", "titles")):
        return 400, {"error": "Paste at least one of: headline, About, experience, README, or post titles."}
    if client is None:
        if HOSTED:
            return 503, {"error": "The AI review isn't turned on for this site yet."}
        return enqueue(data)
    if HOSTED and not take_review_slot([user, ip]):
        return 429, {"error": f"You've used today's {REVIEWS_PER_DAY} AI reviews. Come back tomorrow."}
    try:
        response = client.beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(data)}],
            output_format=Review,
        )
    except anthropic.AuthenticationError:
        return 401, {"error": "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY."}
    except anthropic.RateLimitError:
        return 429, {"error": "Rate limited by the Anthropic API. Try again in a minute."}
    except anthropic.APIStatusError as e:
        return 502, {"error": f"Anthropic API error {e.status_code}: {e.message}"}
    except anthropic.APIConnectionError:
        return 502, {"error": "Couldn't reach the Anthropic API. Check your connection."}

    if response.stop_reason == "refusal":
        return 422, {"error": "Claude declined to review this text."}
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        return 502, {"error": "The review came back incomplete. Try again with shorter input."}
    return 200, response.parsed_output.model_dump()


class Handler(BaseHTTPRequestHandler):
    server_version = "V-Z-ble"
    sys_version = ""

    def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, body: dict):
        self._send(status, json.dumps(body).encode(), "application/json")

    def _user(self) -> str:
        user = self.headers.get("X-VZB-User", "")
        return user if USER_RE.match(user) else ""

    def _ip(self) -> str:
        # Behind Cloud Run's proxy the visitor's address is the first X-Forwarded-For entry.
        forwarded = self.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() if forwarded else self.client_address[0]

    def do_GET(self):
        route = urlparse(self.path)
        query = parse_qs(route.query)
        feeds = {"/api/medium": fetch_medium, "/api/substack": fetch_substack, "/api/leetcode": fetch_leetcode}
        if route.path in ("/", "/index.html"):
            return self._send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        if route.path == EXTENSION_ZIP_PATH:
            return self._send(200, extension_zip(), "application/zip",
                              {"Content-Disposition": f'attachment; filename="{EXTENSION_ZIP_PATH[1:]}"'})
        if route.path == "/health":  # Cloud Run reserves /healthz
            return self._send(200, b"ok", "text/plain")
        if route.path == "/api/status":
            return self._json(200, {"ai": client is not None, "model": MODEL, "hosted": HOSTED,
                                    "publicUrl": PUBLIC_URL, "reviewsPerDay": REVIEWS_PER_DAY if HOSTED else None})
        if route.path.startswith("/api/review/") and not HOSTED:
            return self._json(*queued_result(route.path.rsplit("/", 1)[-1]))
        if route.path in feeds:
            return self._json(*feeds[route.path](query.get("user", [""])[0]))
        if route.path == "/api/photo-hash":
            return self._json(*photo_hash(query.get("url", [""])[0]))
        if route.path == "/api/linkedin":
            user = self._user()
            return self._json(*load_linkedin(user)) if user else self._json(400, {"error": "Missing visitor ID."})
        # Nothing else is served: not the server's code, imports or queued reviews.
        return self._json(404, {"error": "Not found."})

    def do_POST(self):
        routes = {"/api/review": lambda d: review(d, self._user(), self._ip()),
                  "/api/linkedin": lambda d: save_linkedin(d, self._user())}
        if self.path not in routes:
            return self._json(404, {"error": "Not found."})
        if self.path == "/api/linkedin" and not self._user():
            return self._json(400, {"error": "Open V-Z-ble once so the extension can connect, then send again."})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                return self._json(413, {"error": "Request too large."})
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "Request body must be JSON."})
        self._json(*routes[self.path](data))


if __name__ == "__main__":
    host = "0.0.0.0" if HOSTED else "127.0.0.1"
    print(f"V-Z-ble on http://{host}:{PORT}  (hosted: {HOSTED}; AI review: {'on' if client else 'off, set ANTHROPIC_API_KEY'})")
    ThreadingHTTPServer((host, PORT), Handler).serve_forever()
