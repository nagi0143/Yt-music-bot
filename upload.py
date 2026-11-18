import json, os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

M = json.load(open("work/meta.json"))
creds = Credentials(
    None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
    token_uri="https://oauth2.googleapis.com/token",
    client_id=os.environ["YT_CLIENT_ID"],
    client_secret=os.environ["YT_CLIENT_SECRET"],
    scopes=["https://www.googleapis.com/auth/youtube.upload"])
yt = build("youtube", "v3", credentials=creds)

body = {"snippet": {"title": M["title"][:100],
                    "description": M["description"] + "\n\n#aimusic #darkromance #lovesong",
                    "tags": M["tags"][:30], "categoryId": "10",
                    "defaultLanguage": M["lang"]},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}

req = yt.videos().insert(part="snippet,status", body=body,
    media_body=MediaFileUpload("work/final.mp4", chunksize=-1, resumable=True))
res = None
while res is None:
    _, res = req.next_chunk()

yt.thumbnails().set(videoId=res["id"],
    media_body=MediaFileUpload("work/thumb.jpg")).execute()
print("UPLOADED:", res["id"])
