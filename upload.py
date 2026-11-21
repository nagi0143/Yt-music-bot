import json, os
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

M = json.load(open("work/meta.json", encoding="utf-8"))
STATE = json.load(open("state.json", encoding="utf-8")) if Path("state.json").exists() else {"i": 0, "done": []}
STATE.setdefault("done", [])

creds = Credentials(
    None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
    token_uri="https://oauth2.googleapis.com/token",
    client_id=os.environ["YT_CLIENT_ID"],
    client_secret=os.environ["YT_CLIENT_SECRET"],
    scopes=["https://www.googleapis.com/auth/youtube.upload"])
yt = build("youtube", "v3", credentials=creds)

LANGS = {"english": "en", "hindi": "hi", "spanish": "es", "punjabi": "pa", "urdu": "ur"}
first = str(M.get("lang", "english")).lower().split("+")[0].strip()
lang = LANGS.get(first, "en")

tags = [t.replace("<", "").replace(">", "").strip()[:30] for t in M["tags"] if t.strip()][:15]
desc = (M["description"] + "\n\n#aimusic #darkromance #lovesong").replace("<", "").replace(">", "")[:4900]

body = {"snippet": {"title": M["title"].replace("<", "").replace(">", "")[:100],
                    "description": desc,
                    "tags": tags, "categoryId": "10",
                    "defaultLanguage": lang},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}

req = yt.videos().insert(part="snippet,status", body=body,
    media_body=MediaFileUpload("work/final.mp4", chunksize=-1, resumable=True))
res = None
while res is None:
    _, res = req.next_chunk()

yt.thumbnails().set(videoId=res["id"],
    media_body=MediaFileUpload("work/thumb.jpg")).execute()

used = Path("work/used.txt")
if used.exists():
    name = used.read_text(encoding="utf-8").strip()
    if name and name not in STATE["done"]:
        STATE["done"].append(name)
    json.dump(STATE, open("state.json", "w", encoding="utf-8"))

print("UPLOADED: https://youtu.be/" + res["id"])
