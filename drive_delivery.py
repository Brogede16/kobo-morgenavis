"""Drive uploads and recoverable cleanup scoped to generated newspaper EPUBs."""
import base64
import json
import logging
import os
import re
from datetime import date

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)
# The optional "-status" suffix is the one-page failure notice. It is kept under
# the same retention rule, but it is a separate file so it can never replace
# a real edition that already went out that morning.
PATTERN = re.compile(r"^mads-morgen-(\d{4}-\d{2}-\d{2})(?:-status)?\.epub$")
MIME = "application/epub+zip"


def quote(value):
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def list_files(drive, query):
    files, token = [], None
    while True:
        result = drive.files().list(q=query, fields="nextPageToken,files(id,name,mimeType,createdTime)",
                                    pageSize=100, pageToken=token).execute()
        files.extend(result.get("files", []))
        token = result.get("nextPageToken")
        if not token:
            return files


def upload_to_drive(path):
    folder_id = os.environ["GOOGLE_DRIVE_FOLDER_ID"]
    if os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN"):
        credentials = Credentials(token=None, refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
            token_uri="https://oauth2.googleapis.com/token", client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"], scopes=["https://www.googleapis.com/auth/drive.file"])
    else:
        info = json.loads(base64.b64decode(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON_B64"]))
        credentials = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/drive.file"])
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    existing = list_files(drive, f"'{quote(folder_id)}' in parents and name='{quote(path.name)}' and mimeType='{MIME}' and trashed=false")
    metadata = {"name": path.name, "mimeType": MIME, "appProperties": {"generator": "mads-morgen"}}
    media = MediaFileUpload(str(path), mimetype=MIME, resumable=True)
    if existing:
        # parents is not an updatable field on files.update.
        uploaded = drive.files().update(fileId=existing[0]["id"], body=metadata, media_body=media, fields="id,name").execute()
    else:
        uploaded = drive.files().create(body=dict(metadata, parents=[folder_id]), media_body=media, fields="id,name").execute()
    try:
        prune_old_drive_editions(drive, folder_id)
    except Exception as exc:
        logger.warning("Upload succeeded; Drive cleanup failed (%s)", type(exc).__name__)
    return uploaded


def prune_old_drive_editions(drive, folder_id, keep=10):
    if keep < 1:
        raise ValueError("At least one edition must be retained")
    files = list_files(drive, f"'{quote(folder_id)}' in parents and mimeType='{MIME}' and trashed=false")
    editions = []
    for item in files:
        match = PATTERN.fullmatch(item.get("name", ""))
        if not match or item.get("mimeType") != MIME:
            continue
        try:
            day = date.fromisoformat(match[1])
        except ValueError:
            continue
        editions.append((day, item["id"]))
    for _, file_id in sorted(editions, reverse=True)[keep:]:
        drive.files().update(fileId=file_id, body={"trashed": True}).execute()
        logger.info("Moved expired newspaper to Drive trash")
