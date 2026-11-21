import os, sys, re, time
import requests, yaml
from pathlib import Path

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))
GEMINI = os.environ["GEMINI_KEY"]
OUT = Path("lyrics"); OUT.mkdir(exist_ok=True)
_MODELS = []


def list_models():
    global _MODELS
    if _MODELS:
        return _MODELS
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                     params={"key": GEMINI}, timeout=60)
    r.raise_for_status()
    good = []
    for m in r.json().get("models", []):
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
    return good


def gemini(prompt):
    for model in (list_models() or ["gemini-flash-latest"])[:5]:
        for _ in range(2):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": GEMINI},
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"temperature": 1.0,
                                               "maxOutputTokens": 3000}},
                    timeout=(30, 180))
            except Exception:
                time.sleep(8); continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(8); continue
            if r.status_code == 404:
                break
            if not r.ok:
                print(r.text[:300]); break
            parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
            txt = "".join(p.get("text", "") for p in parts).strip()
            if txt:
                return txt
    raise RuntimeError("gemini failed")


def lang_block(code):
    if code == "english":
        return "Write ONLY in English. Script: Latin alphabet. Natural, singable English."
    if "+" in code:
        main, extra = code.split("+", 1)
        return (
            f"Bilingual song. Main language: {CFG['native_names'].get(main, main)} "
            f"(script: {CFG['scripts'].get(main, 'native')}). "
            f"Second language: {CFG['native_names'].get(extra, extra)} "
            f"(script: {CFG['scripts'].get(extra, 'native')}). "
            f"English fills the verses, the second language owns the chorus and hook. "
            f"Never romanise the second language, use its real script.")
    return (f"Write ONLY in {CFG['native_names'].get(code, code)}. "
            f"Script: {CFG['scripts'].get(code, 'native script')}. "
            f"Use everyday spoken words of that language, like a native singer. "
            f"Do NOT romanise, do NOT write in English letters unless that language uses them.")


def main():
    theme = sys.argv[1]
    lang = sys.argv[2] if len(sys.argv) > 2 else "english"
    name = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3].strip() else \
        re.sub(r"[^a-z0-9]+", "-", theme.lower()).strip("-")

    prompt = f"""You are a professional songwriter for the {CFG['channel']} channel.

THEME: {theme}
LANGUAGE RULES: {lang_block(lang)}

PERMANENT IDENTITY:
Dark romantic fantasy pop, trap-soul drums, cinematic indie, atmospheric
electronic. Intimate expressive vocals. Only duet or female solo, never male solo.

Structure: [verse 1] 4 lines / [pre-chorus] 2 lines / [chorus] 4 lines /
[verse 2] 4 lines / [chorus] 4 lines / [bridge] 2 lines / [final chorus] 4 lines

Rules: first person, concrete imagery (rain, neon, ruins, moonlight, velvet,
static, ghosts, ash), one memorable four-word hook repeated word for word in
every chorus, short singable lines, no generic cliches.

Output only the lyrics with square-bracket section markers. No title, no notes.
"""

    text = gemini(prompt)
    path = OUT / f"{name}__{lang.replace('+', '_')}.txt"
    path.write_text(text, encoding="utf-8")
    print(f"SAVED: {path}")
    print("-" * 50)
    print(text)


if __name__ == "__main__":
    main()
