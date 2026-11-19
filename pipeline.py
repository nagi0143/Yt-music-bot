import os, json, random, subprocess, urllib.parse, time
import requests, yaml
from pathlib import Path

CFG = yaml.safe_load(open("config.yaml"))
STATE = json.load(open("state.json")) if Path("state.json").exists() else {"i": 0}
GEMINI = os.environ["GEMINI_KEY"]
FREEAI = os.environ["FREEAI_KEY"]
WORK = Path("work"); WORK.mkdir(exist_ok=True)

MODELS = ["gemini-3.8-flash", "gemini-3-flash", "gemini-2.5-flash"]

def gemini(prompt, json_out=True):
    last = None
    for model in MODELS:
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={GEMINI}")
        for attempt in range(3):
            try:
                r = requests.post(url, json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 1.0, "maxOutputTokens": 4096,
                        **({"responseMimeType": "application/json"} if json_out else {})
                    }}, timeout=(30, 180))
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                time.sleep(10 * (attempt + 1)); continue

            if r.status_code in (429, 500, 502, 503, 504):
                last = r.text
                print(f"{model} busy ({r.status_code}), retry {attempt+1}")
                time.sleep(10 * (attempt + 1)); continue
            if r.status_code == 404:
                print(f"{model} not found, next model"); break
            if not r.ok:
                print("GEMINI ERROR:", r.status_code, r.text); r.raise_for_status()

            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            txt = txt.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(txt) if json_out else txt
    raise RuntimeError(f"All gemini models failed: {last}")

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
        json={"prompt": f"{style}. Dark romantic, emotional, clear intimate lead vocal.",
              "lyrics": lyric_text, "duration": 120, "bpm": 84,
              "instrumental": False},
        timeout=(30, 300))
    if not r.ok:
        print("FREEAI ERROR:", r.status_code, r.text)
        r.raise_for_status()

    d = r.json()
    url = d.get("audio_url") or d.get("url")
    job = d.get("job_id") or d.get("id")

    for _ in range(90):
        if url: break
        if not job: break
        time.sleep(10)
        s = requests.get(f"https://api.free.ai/v1/status/{job}/",
                         headers={"Authorization": f"Bearer {FREEAI}"}, timeout=60)
        j = s.json() if s.ok else {}
        url = j.get("audio_url") or (j.get("result") or {}).get("audio_url")
        if str(j.get("status", "")).lower() in ("failed", "error"):
            raise RuntimeError(f"music job failed: {j}")

    if not url:
        raise RuntimeError(f"no audio url in response: {d}")

    raw = requests.get(url, timeout=300); raw.raise_for_status()
    (WORK / "raw_audio").write_bytes(raw.content)
    subprocess.run(["ffmpeg", "-y", "-i", str(WORK / "raw_audio"),
                    "-c:a", "libmp3lame", "-b:a", "256k",
                    str(WORK / "song.mp3")], check=True)

def cover(theme):
    q = urllib.parse.quote(
        f"cinematic dark romantic fantasy album art, {theme}, no text, "
        "fog, moonlight, volumetric god rays, teal and amber grade, "
        "35mm grain, wide composition, ultra detailed")
    r = requests.get(f"https://image.pollinations.ai/prompt/{q}"
                     f"?width=1920&height=1080&nologo=true&seed={random.randint(1,999999)}",
                     timeout=180)
    r.raise_for_status()
    (WORK / "cover.jpg").write_bytes(r.content)

def thumbnail(title):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(WORK / "cover.jpg").convert("RGB").resize((1280, 720))
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle([0, 430, 1280, 720], fill=(0, 0, 0, 165))
    font = ImageFont.truetype("assets/Anton-Regular.ttf", 92)
    d.text((60, 495), title.upper()[:26], font=font, fill=(255, 245, 230, 255))
    Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB").save(WORK / "thumb.jpg", quality=92)

def video():
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(WORK / "cover.jpg"), "-i", str(WORK / "song.mp3"),
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
    meta = gemini(CFG["lyrics_prompt"].format(lang=lang, theme=theme))
    print("TITLE:", meta["title"])
    music(meta["style_prompt"], meta["lyrics"])
    cover(theme)
    thumbnail(meta["title"])
    video()
    seo = gemini(CFG["seo_prompt"].format(lang=lang, title=meta["title"]))
    json.dump({"lang": lang, "title": seo["title"], "description": seo["description"],
               "tags": seo["tags"]}, open(WORK / "meta.json", "w"), ensure_ascii=False)
    print("READY:", seo["title"])

if __name__ == "__main__":
    main()
