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

STYLE = ("dark fantasy illustration, mysterious, ethereal glowing aura, moonlit mist, "
         "cinematic lighting, rich detail, wide composition")

SCRIPTS = [("hindi", 0x0900, 0x097F), ("punjabi", 0x0A00, 0x0A7F),
           ("russian", 0x0400, 0x04FF), ("korean", 0xAC00, 0xD7AF),
           ("japanese", 0x3040, 0x30FF)]

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
        lang = lang.strip().replace("_", "+")
        theme = theme.strip().replace("_", " ")
        return lang, theme, theme.title()
    if lyr:
        return detect_lang(lyr), "", name.strip()
    return "english", "", name.strip()


def build_title(song, tagline):
    brand = CFG["channel"]
    full = f"{song} | {tagline} | {brand}" if tagline else f"{song} | {brand}"
    if len(full) > 100:
        full = f"{song} | {brand}"
    return full[:100]


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
                    "-c:a", "libmp3lame", "-b:a", "192k",
                    str(WORK / "song.mp3")], check=True)


def cover(stem, image_prompt):
    from PIL import Image, ImageOps

    # 1) tumhari apni image: assets/<gaane ka naam>.jpg / .png / .webp
    key = norm(stem)
    assets = Path("assets")
    if assets.exists():
        for p in sorted(assets.glob("*")):
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and norm(p.stem) == key:
                im = Image.open(p).convert("RGB")
                ImageOps.fit(im, (1920, 1080), centering=(0.5, 0.5)).save(
                    WORK / "cover.jpg", quality=95)
                print("COVER: manual image", p.name)
                return

    # 2) auto image (neeche ka 120px kat jata hai taaki watermark hat jaye)
    q = urllib.parse.quote(f"{image_prompt}, {STYLE}")
    last = None
    for _ in range(3):
        try:
            r = requests.get(
                f"https://image.pollinations.ai/prompt/{q}"
                f"?width=1920&height=1200&model=flux&nologo=true&seed={random.randint(1, 999999)}",
                timeout=180)
            r.raise_for_status()
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
            break
        except Exception as e:
            last = e
            print("COVER RETRY:", e)
            time.sleep(10)
    else:
        raise RuntimeError(f"cover generate nahi hua: {last}")

    ImageOps.fit(im, (1920, 1080), centering=(0.5, 0.0)).save(WORK / "cover.jpg", quality=95)
    print("COVER: auto image")


def load_font(size):
    from PIL import ImageFont
    for path in (FONT, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
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


def thumbnail(song):
    from PIL import Image, ImageDraw
    im = Image.open(WORK / "cover.jpg").convert("RGB").resize((1280, 720))

    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for y in range(330, 720):
        od.line([(0, y), (1280, y)], fill=(0, 0, 0, int(215 * (y - 330) / 390)))
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")

    d = ImageDraw.Draw(im)
    text = song.upper()
    maxw = 1160
    size = 120
    while True:
        font = load_font(size)
        lines = wrap(text, font, maxw, d)
        fits = all(d.textlength(l, font=font) <= maxw for l in lines)
        if (len(lines) <= 2 and fits) or size <= 48:
            break
        size -= 6

    lh = int(size * 1.12)
    y = 720 - 50 - lh * len(lines)
    for line in lines:
        d.text((63, y + 3), line, font=font, fill=(0, 0, 0))
        d.text((60, y), line, font=font, fill=(255, 245, 230))
        y += lh

    d.text((60, 40), CFG["channel"], font=load_font(40), fill=(255, 245, 230))
    im.save(WORK / "thumb.jpg", quality=92)


def video():
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(WORK / "cover.jpg"),
        "-i", str(WORK / "song.mp3"),
        "-filter_complex",
        "[0:v]scale=2400:2400,zoompan=z='min(zoom+0.00035,1.18)'"
        ":d=9999:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps=25,"
        "eq=saturation=1.08:contrast=1.05[v]",
        "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "medium",
        "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
        "-shortest", "-movflags", "+faststart", str(WORK / "video.mp4")
    ], check=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(WORK / "video.mp4"), "-af",
                    f"loudnorm=I={CFG['loudness_lufs']}:TP=-1.5:LRA=11",
                    "-c:v", "copy", str(WORK / "final.mp4")], check=True)


def main():
    files = queue_files()
    if not files:
        print("QUEUE EMPTY — kuch upload karne ko nahi hai")
        return

    f = files[0]
    print("QUEUE:", len(files), "file(s) | picking:", f.name)

    prepare_audio(f)

    lyr, lyr_file = find_lyrics(f.stem)
    print("LYRICS:", lyr_file.name if lyr_file else "NOT FOUND (naam match nahi hua, sirf gaane ke naam se kaam chalega)")

    lang, theme, song = resolve(f, lyr)
    print("SONG:", song)
    print("LANGUAGE:", lang)

    seo = gemini(CFG["seo_prompt"].format(
        channel=CFG["channel"], artist=CFG["artist"], lang=lang, song=song,
        theme=theme or "not given - work it out from the song name and lyrics")
        + f"\n\nLyrics:\n{lyr[:2500]}")
    for k in ("description", "tags"):
        if not seo.get(k):
            raise RuntimeError(f"seo missing {k}")

    title = build_title(song, str(seo.get("tagline", "")).strip())
    theme = str(seo.get("theme") or theme or song)
    image_prompt = str(seo.get("image_prompt") or theme)
    print("THEME:", theme)
    print("TITLE:", title)
    print("IMAGE PROMPT:", image_prompt)

    cover(f.stem, image_prompt)
    thumbnail(song)
    print("ART OK")

    video()
    print("VIDEO OK")

    json.dump({"lang": lang, "theme": theme, "title": title,
               "description": seo["description"], "tags": seo["tags"]},
              open(WORK / "meta.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    (WORK / "used.txt").write_text(f.name, encoding="utf-8")
    print("READY:", title)


if __name__ == "__main__":
    main()
