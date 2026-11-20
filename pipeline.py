
import os
import json
import random
import subprocess
import urllib.parse
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml


# ---------------- CONFIG ----------------

with open("config.yaml", "r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

STATE = (
    json.load(open("state.json", encoding="utf-8"))
    if Path("state.json").exists()
    else {"i": 0}
)

GEMINI = os.environ["GEMINI_KEY"]
FREEAI = os.environ["FREEAI_KEY"]

WORK = Path("work")
WORK.mkdir(exist_ok=True)

MUSIC_SECONDS = int(CFG.get("duration_sec", 150))
FONT = "assets/Anton-Regular.ttf"

_MODELS = []


# ---------------- GEMINI ----------------

def list_models():
    global _MODELS

    if _MODELS:
        return _MODELS

    try:
        r = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": GEMINI},
            timeout=60
        )
        r.raise_for_status()
        items = r.json().get("models", [])

    except Exception as e:
        print("MODEL LIST ERROR:", e)
        return []

    good = []

    for m in items:
        name = m.get("name", "").replace("models/", "")

        if "generateContent" not in m.get(
            "supportedGenerationMethods", []
        ):
            continue

        if any(x in name.lower() for x in (
            "tts", "image", "live", "embedding", "vision"
        )):
            continue

        good.append(name)

    def rank(name):
        score = 0

        if "flash-lite" in name:
            score += 3
        elif "flash" in name:
            score += 2

        if "preview" in name or "exp" in name:
            score -= 1

        return -score

    good.sort(key=rank)

    _MODELS = good

    print("GEMINI MODELS:", good[:6])

    return good


def parse_json(txt):
    txt = txt.strip()

    txt = txt.removeprefix("```json")
    txt = txt.removeprefix("```")
    txt = txt.removesuffix("```").strip()

    try:
        return json.loads(txt)

    except json.JSONDecodeError:
        start = txt.find("{")
        end = txt.rfind("}")

        if start == -1 or end == -1:
            raise

        return json.loads(txt[start:end + 1])


def gemini(prompt):
    models = list_models()

    if not models:
        models = ["gemini-flash-latest"]

    for model in models[:5]:

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )

        for attempt in range(2):

            try:
                r = requests.post(
                    url,
                    params={"key": GEMINI},
                    json={
                        "contents": [{
                            "parts": [{"text": prompt}]
                        }],
                        "generationConfig": {
                            "temperature": 1.0,
                            "maxOutputTokens": 4096,
                            "responseMimeType": "application/json"
                        }
                    },
                    timeout=(30, 180)
                )

            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError
            ) as e:
                print("GEMINI CONNECTION ERROR:", e)
                time.sleep(8)
                continue

            if r.status_code in (429, 500, 502, 503, 504):
                print("GEMINI BUSY:", model, r.status_code)
                time.sleep(8)
                continue

            if r.status_code == 404:
                print("MODEL NOT FOUND:", model)
                break

            if not r.ok:
                print("GEMINI ERROR:", r.status_code, r.text[:500])
                break

            data = r.json()

            candidates = data.get("candidates", [])

            if not candidates:
                print("GEMINI RETURNED NO CANDIDATES:", data)
                break

            parts = candidates[0].get("content", {}).get("parts", [])

            txt = "".join(
                part.get("text", "")
                for part in parts
            )

            if not txt:
                print("GEMINI RETURNED EMPTY TEXT")
                break

            return parse_json(txt)

    raise RuntimeError("Gemini generation failed")


# ---------------- CONTENT ----------------

def pick():
    languages = CFG["languages"]
    themes = CFG["themes"]

    if not languages or not themes:
        raise RuntimeError("Languages or themes are missing")

    i = STATE["i"] % len(languages)

    lang = languages[i]

    theme = themes[
        (i * 3 + random.randint(0, 6)) % len(themes)
    ]

    STATE["i"] = i + 1

    with open("state.json", "w", encoding="utf-8") as f:
        json.dump(STATE, f)

    return lang, theme


def create_song_prompt(lang, theme):

    base_prompt = CFG["lyrics_prompt"].format(
        lang=lang,
        theme=theme
    )

    master_style = CFG.get("master_style", CFG.get("genre_style", ""))

    vocal_rules = CFG.get("vocal_rules", "")

    return f"""
You are creating an original NOXVEILx9 song.

PERMANENT MASTER STYLE:
{master_style}

VOCAL RULES:
{vocal_rules}

LANGUAGE:
{lang}

THEME:
{theme}

IMPORTANT:
The master style must remain recognizable in every song.
The melody, lyrics, atmosphere, arrangement and emotional
direction must be fresh for every new song.

Choose exactly one vocal mode:
duet or female_solo.

Male solo is forbidden.

Return the required JSON format.

ADDITIONAL SONGWRITING INSTRUCTIONS:
{base_prompt}
"""


def validate_song(meta):

    required = [
        "title",
        "lyrics",
        "style_prompt",
        "bpm",
        "vocal_mode"
    ]

    for key in required:
        if key not in meta or meta[key] in ("", None):
            raise RuntimeError(
                f"Gemini output missing required field: {key}"
            )

    mode = str(meta["vocal_mode"]).strip().lower()

    if mode not in ("duet", "female_solo"):
        raise RuntimeError(
            f"Invalid vocal_mode returned by Gemini: {mode}"
        )

    try:
        bpm = int(meta["bpm"])
    except (TypeError, ValueError):
        raise RuntimeError("Gemini returned invalid BPM")

    if not 78 <= bpm <= 105:
        raise RuntimeError(
            f"BPM outside allowed range: {bpm}"
        )

    meta["vocal_mode"] = mode
    meta["bpm"] = bpm

    return meta


# ---------------- MUSIC GENERATION ----------------

def music(style, lyric_text, bpm, vocal_mode):

    base = "https://api.free.ai"

    master_style = CFG.get(
        "master_style",
        CFG.get("genre_style", "")
    )

    genre_style = CFG.get("genre_style", "")

    vocal_rules = CFG.get("vocal_rules", "")

    if vocal_mode == "duet":
        vocal_instruction = """
        Male and female duet vocals.
        Deep youthful powerful male voice.
        Soft expressive natural female voice.
        Distinct vocal sections and natural chemistry.
        Never use male solo vocals.
        """
    else:
        vocal_instruction = """
        Female solo vocals only.
        Soft, sweet, expressive natural female voice.
        No male vocals or male voice.
        """

    final_prompt = f"""
    {master_style}

    Base genre:
    {genre_style}

    Song-specific direction:
    {style}

    Vocal arrangement:
    {vocal_instruction}

    Vocal rules:
    {vocal_rules}

    Create a professionally produced song.
    Keep the NOXVEILx9 signature sound.
    Use an original melody and fresh atmosphere.
    Do not sing production instructions or prompt text.
    """

    print("VOCAL MODE:", vocal_mode)
    print("BPM:", bpm)
    print("DURATION:", MUSIC_SECONDS)

    r = requests.post(
        f"{base}/v1/music/generate/",
        headers={
            "Authorization": f"Bearer {FREEAI}",
            "Content-Type": "application/json"
        },
        json={
            "prompt": final_prompt,
            "lyrics": lyric_text,
            "duration": MUSIC_SECONDS,
            "bpm": bpm,
            "instrumental": False
        },
        timeout=(30, 300)
    )

    if not r.ok:
        print("FREEAI MUSIC ERROR:", r.status_code, r.text[:1000])
        r.raise_for_status()

    data = r.json()

    print("MUSIC RESPONSE:", data)

    audio_url = (
        data.get("audio_url")
        or data.get("url")
    )

    poll_url = data.get("poll_url")

    if data.get("queued") and poll_url:
        poll_url = urljoin(base, poll_url)

    elif data.get("queued") and not poll_url:
        raise RuntimeError(
            "Free.ai queued the job but did not return poll_url"
        )

    # Do not guess a job-status endpoint.
    if data.get("queued") and poll_url:

        for attempt in range(180):

            if audio_url:
                break

            print(
                f"WAITING FOR MUSIC: {attempt + 1}/180"
            )

            time.sleep(10)

            status_response = requests.get(
                poll_url,
                headers={
                    "Authorization": f"Bearer {FREEAI}"
                },
                timeout=60
            )

            if not status_response.ok:
                print(
                    "JOB STATUS ERROR:",
                    status_response.status_code,
                    status_response.text[:1000]
                )

                status_response.raise_for_status()

            job_data = status_response.json()

            print("JOB STATUS:", job_data)

            result = job_data.get("result") or {}

            audio_url = (
                job_data.get("audio_url")
                or job_data.get("url")
                or result.get("audio_url")
                or result.get("url")
            )

            status = str(
                job_data.get("status", "")
            ).lower()

            if status in (
                "failed",
                "error",
                "cancelled"
            ):
                raise RuntimeError(
                    f"Music generation failed: {job_data}"
                )

    if not audio_url:
        raise RuntimeError(
            "Music audio URL not received. "
            f"Initial response: {data}"
        )

    # Download audio

    raw = requests.get(
        audio_url,
        timeout=300
    )

    raw.raise_for_status()

    (WORK / "raw_audio").write_bytes(raw.content)

    # Convert audio

    subprocess.run([
        "ffmpeg",
        "-y",
        "-i", str(WORK / "raw_audio"),
        "-c:a", "libmp3lame",
        "-b:a", "256k",
        str(WORK / "song.mp3")
    ], check=True)

    print("MUSIC DOWNLOAD SUCCESSFUL!")


# ---------------- COVER ----------------

def cover(theme):

    q = urllib.parse.quote(
        f"cinematic dark romantic fantasy album art, {theme}, "
        "no text, fog, moonlight, volumetric god rays, "
        "teal and amber grade, 35mm grain, "
        "wide composition, ultra detailed"
    )

    r = requests.get(
        f"https://image.pollinations.ai/prompt/{q}"
        f"?width=1920&height=1080&nologo=true"
        f"&seed={random.randint(1, 999999)}",
        timeout=180
    )

    r.raise_for_status()

    (WORK / "cover.jpg").write_bytes(r.content)


# ---------------- THUMBNAIL ----------------

def thumbnail(title):

    from PIL import Image, ImageDraw, ImageFont

    im = Image.open(
        WORK / "cover.jpg"
    ).convert("RGB").resize((1280, 720))

    overlay = Image.new(
        "RGBA",
        im.size,
        (0, 0, 0, 0)
    )

    draw = ImageDraw.Draw(overlay)

    draw.rectangle(
        [0, 430, 1280, 720],
        fill=(0, 0, 0, 165)
    )

    font = None

    for path in (
        FONT,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    ):
        try:
            font = ImageFont.truetype(path, 92)
            break
        except Exception:
            continue

    if font is None:
        font = ImageFont.load_default()

    draw.text(
        (60, 495),
        title.upper()[:26],
        font=font,
        fill=(255, 245, 230, 255)
    )

    Image.alpha_composite(
        im.convert("RGBA"),
        overlay
    ).convert("RGB").save(
        WORK / "thumb.jpg",
        quality=92
    )


# ---------------- VIDEO ----------------

def video():

    subprocess.run([
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-i", str(WORK / "cover.jpg"),
        "-i", str(WORK / "song.mp3"),
        "-filter_complex",
        "[0:v]scale=2400:2400,"
        "zoompan=z='min(zoom+0.00035,1.18)'"
        ":d=9999"
        ":x='iw/2-(iw/zoom/2)'"
        ":y='ih/2-(ih/zoom/2)'"
        ":s=1920x1080:fps=25,"
        "eq=saturation=1.08:contrast=1.05[v]",
        "-map", "[v]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "256k",
        "-shortest",
        "-movflags", "+faststart",
        str(WORK / "video.mp4")
    ], check=True)

    subprocess.run([
        "ffmpeg",
        "-y",
        "-i", str(WORK / "video.mp4"),
        "-af",
        f"loudnorm=I={CFG['loudness_lufs']}:TP=-1.5:LRA=11",
        "-c:v", "copy",
        str(WORK / "final.mp4")
    ], check=True)


# ---------------- SEO ----------------

def create_seo(lang, title):

    prompt = CFG["seo_prompt"].format(
        lang=lang,
        title=title
    )

    seo = gemini(prompt)

    for key in ("title", "description", "tags"):
        if key not in seo:
            raise RuntimeError(
                f"SEO response missing: {key}"
            )

    return seo


# ---------------- MAIN ----------------

def main():

    lang, theme = pick()

    print("LANGUAGE:", lang)
    print("THEME:", theme)

    # Generate lyrics and song details

    prompt = create_song_prompt(lang, theme)

    meta = gemini(prompt)

    meta = validate_song(meta)

    print("TITLE:", meta["title"])
    print("VOCAL MODE:", meta["vocal_mode"])
    print("BPM:", meta["bpm"])
    print("LYRICS:\n", meta["lyrics"][:500])

    # Generate music

    music(
        style=meta["style_prompt"],
        lyric_text=meta["lyrics"],
        bpm=meta["bpm"],
        vocal_mode=meta["vocal_mode"]
    )

    print("MUSIC OK")

    # Generate artwork

    cover(theme)

    thumbnail(meta["title"])

    print("ART OK")

    # Generate video

    video()

    print("VIDEO OK")

    # Generate YouTube SEO

    seo = create_seo(
        lang,
        meta["title"]
    )

    output = {
        "lang": lang,
        "theme": theme,
        "title": seo["title"],
        "description": seo["description"],
        "tags": seo["tags"],
        "vocal_mode": meta["vocal_mode"],
        "bpm": meta["bpm"]
    }

    with open(
        WORK / "meta.json",
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    print("READY:", seo["title"])


if __name__ == "__main__":
    main()
