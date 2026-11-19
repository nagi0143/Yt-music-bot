import os, json, random, subprocess, urllib.parse, time
import requests, yaml
from pathlib import Path

CFG = yaml.safe_load(open("config.yaml"))
STATE = json.load(open("state.json")) if Path("state.json").exists() else {"i": 0}
GEMINI = os.environ["GEMINI_KEY"]
FREEAI = os.environ["FREEAI_KEY"]
WORK = Path("work"); WORK.mkdir(exist_ok=True)

MUSIC_SECONDS = int(CFG.get("music_duration", 120))
FONT = "assets/Anton-Regular.ttf"

_MODELS = []


# ---------------- LLM ----------------

def list_models():
    global _MODELS
    if _MODELS:
        return _MODELS
    try:
        r = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models"
            f"?key={GEMINI}", timeout=60)
        items = r.json().get("models", [])
    except Exception as e:
        print("model list failed:", e)
        return []

    good = []
    for m in items:
        name = m.get("name", "").replace("models/", "")
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        if any(x in name for x in ("tts", "image", "live", "embedding", "vision")):
            continue
        good.append(name)

    def rank(n):
        s = 0
        if "flash-lite" in n:
            s += 3
        elif "flash" in n:
            s += 2
        if "preview" in n or "exp" in n:
            s -= 1
        return -s

    good.sort(key=rank)
    _MODELS = good
    print("MODELS FOUND:", good[:6])
    return good


def _parse(txt):
    txt = txt.strip()
    for f in ("```json", "```"):
        txt = txt.removeprefix(f)
    txt = txt.removesuffix("```").strip()
    try:
        return json.loads(txt)
    except Exception:
        i, j = txt.find("{"), txt.rfind("}")
        if i == -1 or j == -1:
            raise
        return json.loads(txt[i:j + 1])


def _gemini(prompt, json_out):
    models = list_models() or ["gemini-flash-latest"]
    for model in models[:5]:
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={GEMINI}")
        for _ in range(2):
            try:
                r = requests.post(url, json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 1.0,
                        "maxOutputTokens": 4096,
                        **({"responseMimeType": "application/json"}
                           if json_out else {})
                    }}, timeout=(30, 180))
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError):
                time.sleep(8)
                continue

            if r.status_code in (429, 500, 502, 503, 504):
                print(f"{model} busy ({r.status_code})")
                time.sleep(8)
                continue
            if r.status_code == 404:
                print(f"{model} not found")
                break
            if not r.ok:
                print("GEMINI ERROR:", r.status_code, r.text[:300])
                break

            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return _parse(txt) if json_out else txt
    raise RuntimeError("gemini exhausted")


def _freeai_chat(prompt, json_out):
    r = requests.post(
        "https://api.free.ai/v1/chat/",
        headers={"Authorization": f"Bearer {FREEAI}",
                 "Content-Type": "application/json"},
        json={"model": "qwen7b",
              "messages": [{"role": "user", "content": prompt}]},
        timeout=(30, 240))
    if not r.ok:
        print("FREEAI CHAT ERROR:", r.status_code, r.text[:300])
        r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    return _parse(txt) if json_out else txt


def llm(prompt, json_out=True):
    try:
        return _gemini(prompt, json_out)
    except Exception as e:
        print("Gemini failed, falling back to free.ai chat:", e)
        return _freeai_chat(prompt, json_out)


# ---------------- content ----------------

def pick():
    i = STATE["i"] % len(CFG["languages"])
    lang = CFG["languages"][i]
    theme = CFG["themes"][(i * 3 + random.randint(0, 6)) % len(CFG["themes"])]
    STATE["i"] = i + 1
    json.dump(STATE, open("state.json", "w"))
    return lang, theme


def music(style, lyric_text):
    r = requests.post(
        "https://api.free.ai/v1/music/generate/",
        headers={"Authorization": f"Bearer {FREEAI}",
                 "Content-Type": "application/json"},
        json={"prompt": f"{style}. Dark romantic, emotional, clear intimate "
                        "lead vocal, wide cinematic mix.",
              "lyrics": lyric_text,
              "duration": MUSIC_SECONDS,
              "bpm": 84,
              "instrumental": False},
        timeout=(30, 300))
    if not r.ok:
        print("FREEAI MUSIC ERROR:", r.status_code, r.text[:500])
        r.raise_for_status()

    d = r.json()
    print("MUSIC RESPONSE KEYS:", list(d.keys()))
    url = d.get("audio_url") or d.get("url")
    job = d.get("job_id") or d.get("id")

    for _ in range(90):
        if url:
            break
        if not job:
            break
        time.sleep(10)
        s = requests.get(
            f"https://api.free.ai/v1/status/{job}/",
            headers={"Authorization": f"Bearer {FREEAI}"}, timeout=60)
        j = s.json() if s.ok else {}
        url = j.get("audio_url") or (j.get("result") or {}).get("audio_url")
        if str(j.get("status", "")).lower() in ("failed", "error"):
            raise RuntimeError(f"music job failed: {j}")

    if not url:
        raise RuntimeError(f"no audio url in response: {d}")

    raw = requests.get(url, timeout=300)
    raw.raise_for_status()
    (WORK / "raw_audio").write_bytes(raw.content)
    subprocess.run(["ffmpeg", "-y", "-i", str(WORK / "raw_audio"),
                    "-c:a", "libmp3lame", "-b:a", "256k",
                    str(WORK / "song.mp3")], check=True)


def cover(theme):
    q = urllib.parse.quote(
        f"cinematic dark romantic fantasy album art, {theme}, no text, "
        "fog, moonlight, volumetric god rays, teal and amber grade, "
        "35mm grain, wide composition, ultra detailed")
    r = requests.get(
        f"https://image.pollinations.ai/prompt/{q}"
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
            font = ImageFont.truetype(path, 92)
            break
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
    lang, theme = pick()
    print("LANG:", lang, "| THEME:", theme)

    meta = llm(CFG["lyrics_prompt"].format(lang=lang, theme=theme))
    print("TITLE:", meta.get("title"))
    print("LYRICS:\n", meta["lyrics"][:400])

    music(meta["style_prompt"], meta["lyrics"])
    print("MUSIC OK")

    cover(theme)
    thumbnail(meta["title"])
    print("ART OK")

    video()
    print("VIDEO OK")

    seo = llm(CFG["seo_prompt"].format(lang=lang, title=meta["title"]))
    json.dump({"lang": lang,
               "title": seo["title"],
               "description": seo["description"],
               "tags": seo["tags"]},
              open(WORK / "meta.json", "w"), ensure_ascii=False)
    print("READY:", seo["title"])


if __name__ == "__main__":
    main()
