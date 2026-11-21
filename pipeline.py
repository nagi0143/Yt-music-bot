import os, json, random, subprocess, urllib.parse, time
import requests, yaml
from pathlib import Path

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))
STATE = json.load(open("state.json", encoding="utf-8")) if Path("state.json").exists() else {"i": 0, "done": []}
STATE.setdefault("done", [])
GEMINI = os.environ["GEMINI_KEY"]
WORK = Path("work"); WORK.mkdir(exist_ok=True)
SONGS = Path("songs")
FONT = "assets/Anton-Regular.ttf"

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


def resolve(f):
    name = f.stem
    if "__" in name:
        lang, theme = name.split("__", 1)
        lang = lang.strip().replace("_", "+")
        theme = theme.strip().replace("_", " ")
    else:
        i = STATE["i"] % len(CFG["languages"])
        lang = CFG["languages"][i]
        STATE["i"] = i + 1
        theme = CFG["themes"][random.randrange(len(CFG["themes"]))]
    return lang, theme


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


def cover(theme):
    q = urllib.parse.quote(
        f"cinematic dark romantic fantasy album art, {theme}, no text, "
        "fog, moonlight, volumetric god rays, teal and amber grade, "
        "35mm grain, wide composition, ultra detailed")
    r = requests.get(f"https://image.pollinations.ai/prompt/{q}"
                     f"?width=1920&height=1080&nologo=true&seed={random.randint(1, 999999)}",
                     timeout=180)
    r.raise_for_status()
    (WORK / "cover.jpg").write_bytes(r.content)


def thumbnail(title):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(WORK / "cover.jpg").convert("RGB").resize((1280, 720))
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle([0, 430, 1280, 720], fill=(0, 0, 0, 165))

    font = None
    for path in (FONT, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(path, 92); break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    d.text((60, 495), title.upper()[:26], font=font, fill=(255, 245, 230, 255))
    Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB").save(
        WORK / "thumb.jpg", quality=92)


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
    lang, theme = resolve(f)
    print("LANGUAGE:", lang)
    print("THEME:", theme)

    side = Path("lyrics") / (f.stem + ".txt")
    lyr = side.read_text(encoding="utf-8")[:1500] if side.exists() else ""

    seo = gemini(CFG["seo_prompt"].format(
        channel=CFG["channel"], lang=lang, theme=theme) + f"\n\nLyrics:\n{lyr}")
    for k in ("title", "description", "tags"):
        if not seo.get(k):
            raise RuntimeError(f"seo missing {k}")
    print("TITLE:", seo["title"])

    cover(theme); thumbnail(seo["title"])
    print("ART OK")

    video()
    print("VIDEO OK")

    json.dump({"lang": lang, "theme": theme, "title": seo["title"],
               "description": seo["description"], "tags": seo["tags"]},
              open(WORK / "meta.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    (WORK / "used.txt").write_text(f.name, encoding="utf-8")
    print("READY:", seo["title"])


if __name__ == "__main__":
    main()
