# Source adaptation: based on existing root-level load_data_local.py in this repository.
import argparse
import json
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import boto3
import requests

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL", "").strip()
MUSIC_TABLE_NAME = os.getenv("MUSIC_TABLE_NAME", "music_shared_songs")


def _clean(value):
    return str(value or "").strip()


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", _clean(text).lower()).strip("-")


def _song_id(title, artist, year):
    return f"{title}#{artist}#{year}"


def _boto3_kwargs():
    kwargs = {"region_name": AWS_REGION}
    if DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL
    return kwargs


def _guess_extension(source_url, content_type):
    ext = Path(urlparse(source_url).path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ext

    guessed = mimetypes.guess_extension(content_type or "")
    return guessed if guessed else ".jpg"


def _upload_cover_to_s3(s3_client, bucket, source_url, key_prefix):
    response = requests.get(source_url, timeout=25)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
    ext = _guess_extension(source_url, content_type)

    parsed_name = Path(urlparse(source_url).path).stem
    if not parsed_name:
        parsed_name = "cover"

    safe_name = _slug(parsed_name)
    object_key = f"{key_prefix.rstrip('/')}/{safe_name}{ext}"

    s3_client.put_object(
        Bucket=bucket,
        Key=object_key,
        Body=response.content,
        ContentType=content_type,
    )

    return object_key


def main():
    parser = argparse.ArgumentParser(description="Load music records into DynamoDB and optionally upload images to S3.")
    parser.add_argument("--file", default="2026a2_songs.json", help="Path to songs JSON file")
    parser.add_argument("--bucket", default=os.getenv("S3_BUCKET_NAME", ""), help="Private S3 bucket for image upload")
    parser.add_argument("--image-prefix", default="music-covers", help="S3 object prefix")
    parser.add_argument("--upload-images", action="store_true", help="Download each img_url and upload to private S3")
    args = parser.parse_args()

    dynamodb = boto3.resource("dynamodb", **_boto3_kwargs())
    table = dynamodb.Table(MUSIC_TABLE_NAME)
    s3_client = boto3.client("s3", region_name=AWS_REGION)

    with open(args.file, "r", encoding="utf-8") as fp:
        songs_payload = json.load(fp)

    songs = songs_payload.get("songs", [])
    loaded = 0

    for song in songs:
        title = _clean(song.get("title"))
        artist = _clean(song.get("artist"))
        year = _clean(song.get("year"))
        album = _clean(song.get("album"))
        source_img = _clean(song.get("img_url") or song.get("image_url"))

        if not title or not artist or not year:
            continue

        artist_year = f"{artist}#{year}"
        song_id = _song_id(title, artist, year)

        item = {
            "title": title,
            "artist_year": artist_year,
            "song_id": song_id,
            "artist": artist,
            "year": year,
            "album": album,
            "img_url": source_img,
        }

        if args.upload_images:
            if not args.bucket:
                raise ValueError("--bucket (or S3_BUCKET_NAME) is required when --upload-images is used")
            if source_img:
                try:
                    key_prefix = f"{args.image_prefix}/{_slug(artist)}-{_slug(title)}-{year}"
                    item["image_key"] = _upload_cover_to_s3(s3_client, args.bucket, source_img, key_prefix)
                except Exception as error:
                    print(f"Image upload failed for '{title}' by '{artist}': {error}")

        table.put_item(Item=item)
        loaded += 1

    print(f"Loaded {loaded} songs into {MUSIC_TABLE_NAME}")


if __name__ == "__main__":
    main()
