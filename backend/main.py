"""Perceptify backend: one file. Fetch comments -> find reaction moments -> ask Gemini -> JSON."""
import json
import math
import os
import random
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()
HERE = Path(__file__).parent
CACHE_DIR = HERE / "cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL = 24 * 3600
MAX_COMMENTS = 500          # fetched
PROMPT_COMMENTS = 320       # sent to the model
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}

app = FastAPI(title="Perceptify")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class UserError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


# ---------------------------------------------------------------- video info
def _fmt_dur(sec):
    return f"{sec // 60}:{sec % 60:02d}" if sec < 3600 else f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _iso_dur(s):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def fetch_meta(video_id):
    key = os.getenv("YOUTUBE_API_KEY")
    if key:
        try:
            r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                             params={"key": key, "id": video_id, "part": "snippet,contentDetails,statistics"}, timeout=20)
            if r.ok and r.json().get("items"):
                it = r.json()["items"][0]
                s = it["snippet"]
                dur = _iso_dur(it["contentDetails"].get("duration"))
                return {"title": s["title"], "channel": s["channelTitle"], "description": s.get("description", ""),
                        "duration": dur, "views": int(it.get("statistics", {}).get("viewCount", 0))}
        except Exception:
            pass
    meta = {"title": "", "channel": "", "description": "", "duration": 0, "views": 0}
    try:  # no-key fallback: oEmbed for title/channel, watch page for description + length
        o = requests.get("https://www.youtube.com/oembed",
                         params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}, headers=UA, timeout=15)
        if o.ok:
            meta["title"], meta["channel"] = o.json().get("title", ""), o.json().get("author_name", "")
        page = requests.get(f"https://www.youtube.com/watch?v={video_id}", headers=UA, timeout=20).text
        m = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', page)
        if m:
            meta["description"] = json.loads(f'"{m.group(1)}"')
        m = re.search(r'"lengthSeconds":"(\d+)"', page)
        if m:
            meta["duration"] = int(m.group(1))
    except Exception:
        pass
    return meta


# ------------------------------------------------------------------ comments
def _parse_votes(v):
    if isinstance(v, (int, float)):
        return int(v)
    v = str(v or "0").strip().upper().replace(",", "")
    try:
        if v.endswith("K"): return int(float(v[:-1]) * 1_000)
        if v.endswith("M"): return int(float(v[:-1]) * 1_000_000)
        return int(float(v))
    except ValueError:
        return 0


def comments_via_api(video_id, key):
    out, token = [], None
    while len(out) < MAX_COMMENTS:
        params = {"key": key, "videoId": video_id, "part": "snippet", "maxResults": 100,
                  "order": "relevance", "textFormat": "plainText"}
        if token:
            params["pageToken"] = token
        r = requests.get("https://www.googleapis.com/youtube/v3/commentThreads", params=params, timeout=25)
        if r.status_code == 403:
            if "commentsDisabled" in r.text:
                raise UserError("Comments are turned off for this video.")
            raise RuntimeError("youtube api 403")
        r.raise_for_status()
        data = r.json()
        for it in data.get("items", []):
            s = it["snippet"]["topLevelComment"]["snippet"]
            out.append({"text": s["textDisplay"], "likes": int(s.get("likeCount", 0)), "author": s.get("authorDisplayName", ""),
                        "replies": int(it["snippet"].get("totalReplyCount", 0))})
        token = data.get("nextPageToken")
        if not token:
            break
    return out[:MAX_COMMENTS]


def comments_via_scraper(video_id):
    from itertools import islice
    from youtube_comment_downloader import SORT_BY_POPULAR, YoutubeCommentDownloader
    dl = YoutubeCommentDownloader()
    gen = dl.get_comments_from_url(f"https://www.youtube.com/watch?v={video_id}", sort_by=SORT_BY_POPULAR)
    out = []
    for c in islice(gen, MAX_COMMENTS * 2):
        if c.get("reply"):
            continue
        out.append({"text": c.get("text", ""), "likes": _parse_votes(c.get("votes")), "author": c.get("author", ""),
                    "replies": 0})
        if len(out) >= MAX_COMMENTS:
            break
    return out


def fetch_comments(video_id):
    key = os.getenv("YOUTUBE_API_KEY")
    comments = []
    if key:
        try:
            comments = comments_via_api(video_id, key)
        except UserError:
            raise
        except Exception:
            comments = []
    if not comments:
        try:
            comments = comments_via_scraper(video_id)
        except Exception as e:
            raise UserError(f"Couldn't load comments for this video ({type(e).__name__}). Try again in a moment.", 502)
    comments = [c for c in comments if c["text"].strip()]
    if not comments:
        raise UserError("This video has no comments to analyse yet.")
    for i, c in enumerate(comments):
        c["w"] = 1 + math.log10(1 + c["likes"]) + 0.3 * math.log10(1 + c["replies"])
        c["text"] = re.sub(r"\s+", " ", c["text"]).strip()
    return comments


# ---------------------------------------------------------------- transcript
def fetch_transcript(video_id):
    """Returns list of (start_seconds, text) or []. Optional: never raises."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        try:
            fetched = YouTubeTranscriptApi().fetch(video_id)
            return [(float(s.start), s.text) for s in fetched]
        except AttributeError:
            return [(float(s["start"]), s["text"]) for s in YouTubeTranscriptApi.get_transcript(video_id)]
    except Exception:
        return []


def transcript_digest(tr, limit=3500):
    if not tr:
        return ""
    text = " ".join(t for _, t in tr)
    if len(text) <= limit:
        return text
    n, size = 6, limit // 6
    step = (len(text) - size) // (n - 1)
    return " […] ".join(text[i * step: i * step + size] for i in range(n))


def transcript_near(tr, sec, window=10):
    return " ".join(t for s, t in tr if sec - window <= s <= sec + window)[:300]


# ------------------------------------------------------------------ timeline
TS = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?([0-5]?\d):([0-5]\d)(?![\d:])(?!\s?[AaPp]\.?[Mm])")


def find_moments(comments, duration, transcript):
    hits = []  # (seconds, weight, comment text)
    for c in comments:
        for m in TS.finditer(c["text"]):
            h, mi, se = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
            sec = h * 3600 + mi * 60 + se
            if sec == 0 or (duration and sec > duration + 2) or sec > 6 * 3600:
                continue
            hits.append((sec, c["w"], c["text"]))
    hits.sort()
    clusters, cur = [], []
    for h in hits:
        if cur and h[0] - cur[-1][0] > 8:
            clusters.append(cur); cur = []
        cur.append(h)
    if cur:
        clusters.append(cur)
    out = []
    for cl in clusters:
        weight = sum(x[1] for x in cl)
        secs = sorted(x[0] for x in cl)
        sec = secs[len(secs) // 2]
        top = sorted(cl, key=lambda x: -x[1])[:4]
        out.append({"sec": sec, "mentions": len(cl), "weight": weight,
                    "comments": [t[:200] for _, _, t in top], "spoken": transcript_near(transcript, sec)})
    out = [o for o in out if o["mentions"] >= 2] or out  # prefer repeated reactions
    out.sort(key=lambda o: -o["weight"])
    return sorted(out[:5], key=lambda o: o["sec"])


# --------------------------------------------------------------------- Gemini
TONES = ["hype", "funny", "angry", "wholesome", "sad", "divided", "critical", "nostalgic", "chill", "awe"]
MODELS = []


def _models():
    pref = [os.getenv("GEMINI_MODEL", "").strip(), "gemini-flash-latest", "gemini-2.5-flash", "gemini-flash-lite-latest", "gemini-2.0-flash"]
    seen, out = set(), []
    for m in pref:
        if m and m not in seen:
            seen.add(m); out.append(m)
    return out


SYSTEM = f"""You analyse the comment section of one YouTube video and explain what the AUDIENCE actually thinks.
Rules:
- Comments are listed as: id | likes | text. Likes = how much the crowd agrees. Weigh liked comments more.
- Understand sarcasm, memes and in-jokes; never read ironic praise literally. Comments may be in any language or mixed (e.g. Hinglish); understand them all.
- Write your analysis in English.
- Judge the audience, not the video. Be specific and punchy, not generic. No filler like "viewers have mixed feelings".
- Evidence ids MUST be ids from the list. Pick the most representative, funniest or sharpest comments.
- Return ONLY valid JSON, exactly this shape:
{{
 "mood": {{"label": "2-4 word mood headline, sharp and specific, e.g. 'Roasting the sponsor'", "tone": one of {TONES}}},
 "verdict": "one sharp sentence: what the crowd is really saying",
 "tldr": "2-3 sentences summarising audience reaction",
 "sentiment": {{"positive": 0-1, "mixed": 0-1, "negative": 0-1}},
 "anger_at_creator": {{"score": 0-100, "reason": "short; say 'none' if people are not angry at the creator"}},
 "clickbait": {{"score": 0-100, "verdict": "Delivered|Mostly delivered|Mixed|Clickbait", "reason": "did the video deliver what title/description promised, per the audience"}},
 "themes": [exactly 3 of {{"title": "<=6 words", "summary": "1-2 sentences", "share": 0-1, "sentiment": -1..1, "evidence_ids": ["c1","c2","c3"]}}],
 "moments": [one per reaction moment given, in same order: {{"i": index, "label": "what happened/why people reacted, <=8 words", "emotion": "laugh|anger|awe|sad|hype|shock|other"}}],
 "running_jokes": ["up to 3 short in-jokes or repeated phrases, [] if none"]
}}"""


def build_prompt(meta, sample, moments, transcript):
    parts = [f"VIDEO TITLE: {meta['title']}", f"CHANNEL: {meta['channel']}",
             f"DESCRIPTION: {meta['description'][:1200]}"]
    digest = transcript_digest(transcript)
    if digest:
        parts.append(f"TRANSCRIPT (sampled): {digest}")
    if moments:
        lines = []
        for i, m in enumerate(moments):
            lines.append(f"[{i}] at {_fmt_dur(m['sec'])} ({m['mentions']} comments mention it). Spoken then: {m['spoken'] or 'n/a'}. "
                         f"Comments: " + " || ".join(m["comments"]))
        parts.append("REACTION MOMENTS (timestamps people referenced):\n" + "\n".join(lines))
    parts.append("COMMENTS:\n" + "\n".join(f"{c['id']} | {c['likes']} | {c['text'][:260]}" for c in sample))
    return "\n\n".join(parts)


def call_gemini(prompt):
    key = os.getenv("GEMINI_API_KEY", "").strip().strip("\"'")
    if not key:
        raise UserError("GEMINI_API_KEY is missing. Add it to backend/.env and restart the server.", 500)
    last = ""
    for model in _models():
        for attempt in range(2):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json={"systemInstruction": {"parts": [{"text": SYSTEM}]},
                          "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                          "generationConfig": {"responseMimeType": "application/json", "temperature": 0.5 if attempt == 0 else 0.2, "maxOutputTokens": 8192}},
                    timeout=90)
            except requests.RequestException as e:
                last = str(e); time.sleep(1); continue
            if r.status_code in (404, 400) and "API key" not in r.text:
                last = f"{model}: {r.status_code}"; break           # model name not available -> next model
            if r.status_code in (429, 503):
                last = f"{model}: busy"
                if attempt == 0:
                    time.sleep(4); continue
                break
            if r.status_code in (401, 403) or "API key not valid" in r.text:
                try:
                    detail = r.json()["error"]["message"]
                except Exception:
                    detail = r.text[:200]
                raise UserError(f"Gemini says: {detail}", 500)
            if not r.ok:
                last = f"{model}: {r.status_code}"; break
            try:
                text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
                text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
                return json.loads(text[text.find("{"): text.rfind("}") + 1])
            except Exception as e:
                last = f"bad json from {model}: {e}"
                if attempt == 0:
                    continue
                break
    raise UserError(f"The AI is busy or unavailable right now ({last}). Wait ~30s and press Retry.", 503)


# ------------------------------------------------------------------- assemble
def _clamp(x, lo, hi, default):
    try:
        return max(lo, min(hi, float(x)))
    except (TypeError, ValueError):
        return default


def mock_report(meta):
    return {"mood": {"label": "Roasting the sponsor", "tone": "funny"},
            "verdict": "Everyone loved the video and is mostly here to mock the ad read.",
            "tldr": "The crowd enjoyed the video, but the sponsor segment became the main joke. A loud minority wants a part 2.",
            "sentiment": {"positive": 0.55, "mixed": 0.25, "negative": 0.2},
            "anger_at_creator": {"score": 18, "reason": "Mild annoyance at the long ad read"},
            "clickbait": {"score": 82, "verdict": "Mostly delivered", "reason": "People say the title matched the content."},
            "themes": [{"title": "Sponsor segment roasted", "summary": "The ad read got mocked relentlessly.", "share": 0.4, "sentiment": -0.2, "evidence_ids": ["c1", "c2"]},
                       {"title": "Wants a part two", "summary": "Viewers are begging for a sequel.", "share": 0.35, "sentiment": 0.8, "evidence_ids": ["c3", "c4"]},
                       {"title": "Editing is elite", "summary": "Praise for pacing and cuts.", "share": 0.25, "sentiment": 0.9, "evidence_ids": ["c5", "c6"]}],
            "moments": [{"i": 0, "label": "The unexpected plot twist", "emotion": "shock"}, {"i": 1, "label": "The ad read meltdown", "emotion": "laugh"}],
            "running_jokes": ["Part 2 when?", "the ad read"]}


def assemble(video_id, meta, comments, moments, spec, transcript_used, engine):
    by_id = {c["id"]: c for c in comments}
    themes = []
    for t in (spec.get("themes") or [])[:3]:
        ev, seen = [], set()
        for cid in t.get("evidence_ids") or []:
            c = by_id.get(str(cid))
            if c and c["text"] not in seen:
                seen.add(c["text"]); ev.append({"text": c["text"][:400], "likes": c["likes"], "author": c["author"]})
        if len(ev) < 2:  # never show an empty evidence list: backfill with the top real comments
            for c in comments[:12]:
                if c["text"] not in seen and len(ev) < 3:
                    seen.add(c["text"]); ev.append({"text": c["text"][:400], "likes": c["likes"], "author": c["author"]})
        themes.append({"title": str(t.get("title", "Theme"))[:60], "summary": str(t.get("summary", ""))[:300], "share": _clamp(t.get("share"), 0, 1, 0.3),
                       "sentiment": _clamp(t.get("sentiment"), -1, 1, 0), "evidence": ev[:4]})
    mo = []
    labels = {int(x.get("i", -1)): x for x in spec.get("moments") or [] if str(x.get("i", "")).lstrip("-").isdigit()}
    for i, m in enumerate(moments):
        x = labels.get(i, {})
        mo.append({"sec": m["sec"], "time": _fmt_dur(m["sec"]), "mentions": m["mentions"],
                   "label": str(x.get("label") or "Audience reacted here")[:80], "emotion": str(x.get("emotion") or "other")})
    mood = spec.get("mood") or {}
    tone = mood.get("tone") if mood.get("tone") in TONES else "chill"
    s = spec.get("sentiment") or {}
    sp, sm, sn = (_clamp(s.get(k), 0, 1, 0) for k in ("positive", "mixed", "negative"))
    tot = (sp + sm + sn) or 1
    ac, cb = spec.get("anger_at_creator") or {}, spec.get("clickbait") or {}
    return {
        "video_id": video_id,
        "video": {"title": meta["title"], "channel": meta["channel"], "duration": meta["duration"]},
        "mood": {"label": str(mood.get("label") or "Mixed bag")[:40], "tone": tone},
        "verdict": str(spec.get("verdict", ""))[:240],
        "tldr": str(spec.get("tldr", ""))[:700],
        "sentiment": {"positive": sp / tot, "mixed": sm / tot, "negative": sn / tot},
        "anger": {"score": int(_clamp(ac.get("score"), 0, 100, 0)), "reason": str(ac.get("reason", ""))[:200]},
        "clickbait": {"score": int(_clamp(cb.get("score"), 0, 100, 50)), "verdict": str(cb.get("verdict", "Mixed"))[:30],
                      "reason": str(cb.get("reason", ""))[:220]},
        "themes": themes, "moments": mo,
        "running_jokes": [str(j)[:60] for j in (spec.get("running_jokes") or [])[:3]],
        "stats": {"comments": len(comments), "transcript": transcript_used, "engine": engine},
    }


def analyse(video_id):
    meta = fetch_meta(video_id)
    comments = fetch_comments(video_id)
    comments.sort(key=lambda c: -c["w"])
    for i, c in enumerate(comments):
        c["id"] = f"c{i + 1}"
    transcript = fetch_transcript(video_id)
    moments = find_moments(comments, meta["duration"], transcript)
    top = comments[: int(PROMPT_COMMENTS * 0.75)]
    rest = comments[int(PROMPT_COMMENTS * 0.75):]
    random.Random(7).shuffle(rest)
    sample = top + rest[: PROMPT_COMMENTS - len(top)]
    spec = call_gemini(build_prompt(meta, sample, moments, transcript))
    return assemble(video_id, meta, comments, moments, spec, bool(transcript), "gemini")


# ------------------------------------------------------------------------ API
_locks = defaultdict(threading.Lock)


@app.get("/health")
def health():
    return {"ok": True, "mock": os.getenv("MOCK_MODE") == "1", "gemini_key": bool(os.getenv("GEMINI_API_KEY"))}


@app.get("/analyze")
def analyze(video_id: str, refresh: int = 0):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return JSONResponse({"error": "That doesn't look like a YouTube video."}, status_code=400)
    path = CACHE_DIR / f"{video_id}.v3.json"
    try:
        with _locks[video_id]:
            if not refresh and path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL:
                return json.loads(path.read_text())
            if os.getenv("MOCK_MODE") == "1":
                time.sleep(2)
                comments = [{"id": f"c{i}", "text": t, "likes": 1000 - i * 50, "author": f"viewer{i}"} for i, t in enumerate(
                    ["Part 2 when??", "the ad read killed me 💀", "editing on this is insane", "who's here in 2026?", "this deserves a million views", "the plot twist at 2:14 got me", "bro really said that with a straight face"], 1)]
                report = assemble(video_id, {"title": "Demo video", "channel": "Demo", "duration": 600}, comments,
                                  [{"sec": 134, "mentions": 9}, {"sec": 301, "mentions": 6}], mock_report({}), False, "mock")
            else:
                report = analyse(video_id)
            path.write_text(json.dumps(report))
            return report
    except UserError as e:
        return JSONResponse({"error": str(e)}, status_code=e.status)
    except Exception as e:  # never leak a stack trace to the extension
        return JSONResponse({"error": f"Unexpected error: {type(e).__name__}. Check the terminal running the backend."}, status_code=500)
