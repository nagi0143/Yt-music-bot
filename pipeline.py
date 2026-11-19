import os, json, random, subprocess, urllib.parse, time
import requests, yaml
from pathlib import Path

CFG = yaml.safe_load(open("config.yaml"))
STATE = json.load(open("state.json")) if Path("state.json").exists() else {"i": 0}
GEMINI = os.environ["GEMINI_KEY"]
WORK = Path("work"); WORK.mkdir(exist_ok=True)



def gemini(prompt, json_out=True):
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-3.8-flash:generateContent?key={GEMINI}"
    )

    for attempt in range(5):
        try:
            r = requests.post(
                url,
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 1.0,
                        "maxOutputTokens": 4096,
                        **({"responseMimeType": "application/json"} if json_out else {})
                    }
                },
                timeout=(30, 300)
            )

            if r.status_code in (429, 500, 502, 503, 504):
                print("GEMINI TEMPORARY ERROR:", r.status_code, r.text)
                if attempt < 4:
                    wait = 15 * (attempt + 1)
                    print(f"Retrying in {wait} seconds...")
                    time.sleep(wait)
                    continue

            if not r.ok:
                print("GEMINI ERROR:", r.status_code, r.text)
                r.raise_for_status()

            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(txt) if json_out else txt

        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError):
            if attempt == 4:
                raise
            wait = 15 * (attempt + 1)
            print(f"Gemini connection retry in {wait} seconds...")
            time.sleep(wait)



def pick():
    i = STATE["i"] % len(CFG["languages"])
    lang = CFG["languages"][i]
    theme = CFG["themes"][(i * 3 + random.randint(0, 6)) % len(CFG["themes"])]
    STATE["i"] = i + 1
    json.dump(STATE, open("state.json", "w"))
    return lang, theme


def music(style, lyric_text):
    r = requests.post(
        "https://api.elevenlabs.io/v1/music",
        headers={
            "xi-api-key": os.environ["ELEVEN_KEY"],
            "Content-Type": "application/json"
        },
        json={
            "prompt": f"{style}. {lyric_text[:1200]}",
            "music_length_ms": CFG["duration_sec"] * 1000
        },
        timeout=(30, 300)
    )

    if not r.ok:
        print("ELEVENLABS ERROR:", r.status_code, r.text)
        r.raise_for_status()

    return r.content


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
    (WORK / "song.mp3").write_bytes(music(meta["style_prompt"], meta["lyrics"]))
    cover(theme)
    thumbnail(meta["title"])
    video()
    seo = gemini(CFG["seo_prompt"].format(lang=lang, title=meta["title"]))
    json.dump({"lang": lang, "title": seo["title"], "description": seo["description"],
               "tags": seo["tags"]}, open(WORK / "meta.json", "w"), ensure_ascii=False)
    print("READY:", seo["title"])

if __name__ == "__main__":
    main()
