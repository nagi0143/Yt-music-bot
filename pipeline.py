import os, re, io, json, random, subprocess, urllib.parse, time
import requests, yaml
from pathlib import Path

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))
STATE = json.load(open("state.json", encoding="utf-8")) if Path("state.json").exists() else {"i": 0, "done": []}
STATE.setdefault("done", [])
GEMINI = os.environ["GEMINI_KEY"]
WORK = Path("work"); WORK.mkdir(exist_ok=True)
SONGS = Path("songs")
FONT = "assets/Anton-Regular.ttf"

W, H = 3840, 2160
GEN_W, GEN_H = 2048, 1152

SCRIPTS = [("hindi", 0x0900, 0x097F), ("punjabi", 0x0A00, 0x0A7F),
           ("russian", 0x0400, 0x04FF), ("korean", 0xAC00, 0xD7AF),
           ("japanese", 0x3040, 0x30FF)]

SMALL = {"a", "an", "the", "in", "of", "to", "and", "at", "on", "for",
         "with", "my", "your", "is", "are", "be", "by"}

VIBE_STYLE = {
    "romantic": "warm golden-hour light, soft bokeh, dreamy haze, tender mood",
    "sensual": "low warm lamp light, deep shadows, close intimate atmosphere, silk tones",
    "dark_obsession": "cold moonlight, deep crimson highlights, gothic heavy mood",
    "melancholic": "desaturated blue-grey, rain streaks, fog, soft lonely contrast",
    "epic": "dramatic sky, volumetric god rays, vast scale, cinematic grandeur",
    "party": "neon magenta and cyan, glowing lights, motion blur, high energy",
    "chill": "cool teal dusk, still air, calm haze, relaxed mood",
    "dreamy": "pastel mist, floating particles, soft surreal glow",
    "nostalgic": "faded warm tones, film grain, late afternoon, memory feel",
}

VIBE_FONT = {
    "romantic": "script", "sensual": "script", "dreamy": "script",
    "dark_obsession": "serif", "melancholic": "serif", "nostalgic": "serif",
    "epic": "display", "party": "display", "chill": "display",
}

VIBE_COLOR = {
    "romantic": ((255, 236, 226), (240, 120, 140)),
    "sensual": ((255, 226, 216), (200, 80, 92)),
    "dark_obsession": ((245, 240, 235), (198, 45, 45)),
    "melancholic": ((232, 238, 246), (118, 150, 182)),
    "epic": ((250, 245, 230), (200, 150, 60)),
    "party": ((255, 255, 255), (255, 48, 150)),
    "chill": ((236, 246, 251), (88, 170, 200)),
    "dreamy": ((246, 236, 255), (170, 130, 220)),
    "nostalgic": ((245, 236, 221), (180, 140, 100)),
}

_MODELS = []


def list_models():
    global _MODELS
    if _MODELS:
        return _MODELS
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": GEMINI}, timeout=60)
        r.raise_for_status()
        items = r.json().get("models", [])
    except Exception as e:
        print("MODEL LIST ERROR:", e)
        return []

    good = []
    for m in items:
        name = m.get("name", "").replace("models/", "")
        low = name.lower()
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        if any(x in low for x in ("tts", "image", "live", "embedding", "vision", "lyria")):
            continue
        good.append(name)

    good.sort(key=lambda n: -((3 if "flash-lite" in n else 2 if "flash" in n else 0)
                              - (1 if "preview" in n or "exp" in n else 0)))
    _MODELS = good
    print("GEMINI MODELS:", good[:6])
    return good


def parse_json(txt):
    txt = txt.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        i, j = txt.find("{"), txt.rfind("}")
        if i == -1 or j == -1:
            raise
        return json.loads(txt[i:j + 1])


def gemini(prompt):
    models = list_models() or ["gemini-flash-latest"]
    for model in models[:5]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for _ in range(2):
            try:
                r = requests.post(url, params={"key": GEMINI}, json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 1.0,
                                         "maxOutputTokens": 4096,
                                         "responseMimeType": "application/json"}},
                    timeout=(30, 180))
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                time.sleep(8); continue

            if r.status_code in (429, 500, 502, 503, 504):
                print("GEMINI BUSY:", model, r.status_code); time.sleep(8); continue
            if r.status_code == 404:
                print("MODEL NOT FOUND:", model); break
            if not r.ok:
                print("GEMINI ERROR:", r.status_code, r.text[:400]); break

            parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
            txt = "".join(p.get("text", "") for p in parts)
            if not txt:
                break
            return parse_json(txt)
    raise RuntimeError("Gemini text generation failed")


def queue_files():
    done = set(STATE["done"])
    return sorted(p for p in SONGS.glob("*.mp3") if p.name not in done)


def norm(s):
    return re.sub(r"[\W_]+", "", s.lower())


def clean_name(s):
    words = re.sub(r"[\W_]+", " ", s).strip().split()
    out = []
    for i, w in enumerate(words):
        w = w.lower()
        out.append(w if (i > 0 and w in SMALL) else w.capitalize())
    t = " ".join(out)
    return (t[0].upper() + t[1:]) if t else t


def thumb_words(song, n=3):
    parts = re.findall(r"[^\W\d_]+", song)
    keep = [p for p in parts if p.lower() not in SMALL] or parts
    return " ".join(keep[:n])


def has_deva(s):
    return any(0x0900 <= ord(c) <= 0x097F for c in s)


def find_lyrics(stem):
    d = Path("lyrics")
    if not d.exists():
        return "", None
    key = norm(stem)
    for p in sorted(d.glob("*.txt")):
        a = norm(p.stem)
        if a and (a == key or (len(a) >= 12 and key.startswith(a))):
            return p.read_text(encoding="utf-8"), p
    return "", None


def detect_lang(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "english"
    best, share = "english", 0.0
    for name, lo, hi in SCRIPTS:
        n = sum(1 for c in letters if lo <= ord(c) <= hi)
        if n / len(letters) > share:
            best, share = name, n / len(letters)
    return best if share > 0.25 else "english"


def resolve(f, lyr):
    name = f.stem
    if "__" in name:
        lang, theme = name.split("__", 1)
        return lang.strip().replace("_", "+"), clean_name(theme), clean_name(theme)
    if lyr:
        return detect_lang(lyr), "", clean_name(name)
    return "english", "", clean_name(name)


def build_title(song, tagline):
    brand = CFG["channel"]
    t = " ".join(str(tagline).split()[:6]).strip(" |,-")
    base = f"{song} | {t}" if t else song
    if len(base) + len(brand) + 3 <= 72:
        base = f"{base} | {brand}"
    return base[:100]


def prepare_audio(src):
    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(src)],
        capture_output=True, text=True).stdout.strip()
    try:
        seconds = float(dur)
    except ValueError:
        seconds = 0
    print("SOURCE DURATION:", seconds)
    if seconds < 30:
        raise RuntimeError(f"song bahut chhoti hai: {seconds}s")

    subprocess.run(["ffmpeg", "-y", "-i", str(src),
                    "-c:a", "libmp3lame", "-b:a", "320k",
                    str(WORK / "song.mp3")], check=True)


def trim_bars(im, tol=20):
    g = im.convert("L")
    w, h = g.size
    px = g.load()
    sx, sy = max(1, w // 80), max(1, h // 80)

    def row_dark(y):
        return all(px[x, y] < tol for x in range(0, w, sx))

    top = 0
    while top < h - 20 and row_dark(top):
        top += 1
    bot = h - 1
    while bot > top + 20 and row_dark(bot):
        bot -= 1

    def col_dark(x):
        return all(px[x, y] < tol for y in range(top, bot, sy))

    left = 0
    while left < w - 20 and col_dark(left):
        left += 1
    right = w - 1
    while right > left + 20 and col_dark(right):
        right -= 1

    if right - left < w * 0.55 or bot - top < h * 0.55:
        return im
    print("BAR TRIM:", (left, top, right, bot), "from", (w, h))
    return im.crop((left, top, right + 1, bot + 1))


def finish(im):
    from PIL import Image, ImageOps, ImageFilter
    im = trim_bars(im)
    im = ImageOps.fit(im, (W, H), method=Image.LANCZOS, centering=(0.5, 0.42))
    im = im.filter(ImageFilter.UnsharpMask(radius=1.8, percent=155, threshold=2))
    im.save(WORK / "cover.jpg", quality=96, subsampling=0)


def cover(stem, scene, vibe):
    from PIL import Image

    key = norm(stem)
    assets = Path("assets")
    if assets.exists():
        for p in sorted(assets.glob("*")):
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and norm(p.stem) == key:
                finish(Image.open(p).convert("RGB"))
                print("COVER: manual", p.name)
                return

    style = VIBE_STYLE.get(vibe, VIBE_STYLE["dark_obsession"])
    q = urllib.parse.quote(f"{scene}, {style}")
    last = None
    for _ in range(3):
        try:
            r = requests.get(
                f"https://image.pollinations.ai/prompt/{q}"
                f"?width={GEN_W}&height={GEN_H}&model=flux&nologo=true"
                f"&enhance=true&seed={random.randint(1, 999999)}",
                timeout=240)
            r.raise_for_status()
            finish(Image.open(io.BytesIO(r.content)).convert("RGB"))
            print("COVER: auto", vibe, "->", W, "x", H)
            return
        except Exception as e:
            last = e
            print("COVER RETRY:", e)
            time.sleep(10)
    raise RuntimeError(f"cover fail: {last}")


def load_font(size, kind="display"):
    from PIL import ImageFont
    table = {
        "display": ["assets/BebasNeue-Regular.ttf", FONT,
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"],
        "script": ["assets/GreatVibes-Regular.ttf", FONT,
                   "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"],
        "serif": ["/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", FONT],
        "deva": ["assets/NotoSansDevanagari.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", FONT],
    }
    for path in table.get(kind, table["display"]):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def wrap(text, font, maxw, d):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def thumbnail(song, kicker, vibe):
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.open(WORK / "cover.jpg").convert("RGB").resize(
        (1280, 720), Image.LANCZOS)

    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for y in range(720):
        a = 40 + int(200 * max(0, y - 250) / 470)
        od.line([(0, y), (1280, y)], fill=(0, 0, 0, min(a, 235)))
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(im)

    text = thumb_words(song)
    kind = "deva" if has_deva(text) else VIBE_FONT.get(vibe, "display")
    ink, accent = VIBE_COLOR.get(vibe, ((255, 250, 242), (200, 60, 60)))

    bf = load_font(24, "display")
    bw = d.textlength(CFG["channel"], font=bf)
    d.rounded_rectangle([46, 40, 46 + bw + 40, 82], radius=21, fill=(0, 0, 0, 160))
    d.text((66, 48), CFG["channel"], font=bf, fill=(240, 233, 222))

    kf = load_font(30, "display")
    kick = kicker.upper()[:26]
    kw = d.textlength(kick, font=kf)
    d.rectangle([48, 96, 48 + kw + 26, 138], fill=accent)
    d.text((61, 103), kick, font=kf, fill=(20, 14, 14))

    maxw = 1080
    size = 140 if kind != "script" else 168
    while True:
        font = load_font(size, kind)
        lines = wrap(text, font, maxw, d)
        if len(lines) <= 2 or size <= 58:
            break
        size -= 8

    lh = int(size * (0.95 if kind == "script" else 1.06))
    y = 650 - lh * len(lines)
    d.rectangle([56, y - 26, 150, y - 18], fill=accent)
    for line in lines:
        d.text((60, y + 4), line, font=font, fill=(0, 0, 0))
        d.text((56, y), line, font=font, fill=ink)
        y += lh

    im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=90, threshold=2)).save(
        WORK / "thumb.jpg", quality=95, subsampling=0)


def video():
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(WORK / "cover.jpg"),
        "-i", str(WORK / "song.mp3"),
        "-filter_complex",
        f"[0:v]scale={W}:{H}:flags=lanczos,"
        "zoompan=z='min(zoom+0.00028,1.14)'"
        f":d=9999:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps=24,"
        "unsharp=5:5:0.55:5:5:0.0,eq=saturation=1.06:contrast=1.04[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-profile:v", "high", "-level", "5.1", "-pix_fmt", "yuv420p",
        "-tune", "stillimage", "-threads", "0",
        "-c:a", "aac", "-b:a", "320k",
        "-shortest", "-movflags", "+faststart", str(WORK / "video.mp4")
    ], check=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(WORK / "video.mp4"), "-af",
                    f"loudnorm=I={CFG['loudness_lufs']}:TP=-1.5:LRA=11",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "320k",
                    str(WORK / "final.mp4")], check=True)


def main():
    files = queue_files()
    if not files:
        print("QUEUE EMPTY")
        return

    f = files[0]
    print("QUEUE:", len(files), "| picking:", f.name)

    prepare_audio(f)
    lyr, lyr_file = find_lyrics(f.stem)
    print("LYRICS:", lyr_file.name if lyr_file else "NOT FOUND")

    lang, theme, song = resolve(f, lyr)
    print("SONG:", song, "| LANG:", lang)

    seo = gemini(CFG["seo_prompt"].format(
        channel=CFG["channel"], artist=CFG["artist"], lang=lang, song=song,
        theme=theme or "work it out from the name and lyrics")
        + f"\n\nLyrics:\n{lyr[:2500]}")

    for k in ("description", "tags", "scene_prompt"):
        if not seo.get(k):
            raise RuntimeError(f"seo missing {k}")

    vibe = str(seo.get("vibe", "")).strip().lower().replace(" ", "_")
    if vibe not in VIBE_STYLE:
        vibe = "dark_obsession"

    title = build_title(song, str(seo.get("tagline", "")).strip())
    kicker = str(seo.get("kicker") or vibe.replace("_", " ")).strip()
    scene = str(seo["scene_prompt"]).strip()

    print("VIBE:", vibe, "| KICKER:", kicker)
    print("TITLE:", title)

    cover(f.stem, scene, vibe)
    thumbnail(song, kicker, vibe)
    print("ART OK")

    video()
    print("VIDEO OK")

    json.dump({"lang": lang, "theme": theme, "vibe": vibe, "title": title,
               "description": seo["description"], "tags": seo["tags"]},
              open(WORK / "meta.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    (WORK / "used.txt").write_text(f.name, encoding="utf-8")
    print("READY:", title)


if __name__ == "__main__":
    main()
