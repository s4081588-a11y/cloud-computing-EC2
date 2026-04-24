# Source adaptation: based on existing root-level app.py in this repository.
import os
import re
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)

cors_origins = [origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if origin.strip()]
CORS(app, resources={r"/*": {"origins": cors_origins}})

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL", "").strip()
USERS_TABLE_NAME = os.getenv("USERS_TABLE_NAME", "music_shared_users")
MUSIC_TABLE_NAME = os.getenv("MUSIC_TABLE_NAME", "music_shared_songs")
SUBSCRIPTIONS_TABLE_NAME = os.getenv("SUBSCRIPTIONS_TABLE_NAME", "music_shared_subscriptions")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "")
PRESIGNED_URL_TTL = int(os.getenv("PRESIGNED_URL_TTL", "3600"))


def _boto3_kwargs():
    kwargs = {"region_name": AWS_REGION}
    if DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL
    return kwargs


dynamodb = boto3.resource("dynamodb", **_boto3_kwargs())
s3_client = boto3.client("s3", region_name=AWS_REGION)

users_table = dynamodb.Table(USERS_TABLE_NAME)
music_table = dynamodb.Table(MUSIC_TABLE_NAME)
subs_table = dynamodb.Table(SUBSCRIPTIONS_TABLE_NAME)


def _clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _build_regex_pattern(value):
    text = _clean_text(value)
    if not text:
        return None

    if text.lower().startswith("re:"):
        raw_pattern = text[3:].strip()
        if not raw_pattern:
            return None
        try:
            return re.compile(raw_pattern, re.IGNORECASE)
        except re.error:
            pass

    tokens = [re.escape(token) for token in re.split(r"\s+", text) if token]
    if not tokens:
        return None

    pattern = "".join(f"(?=.*{token})" for token in tokens) + ".*"
    return re.compile(pattern, re.IGNORECASE)


def _regex_match(pattern, value):
    if pattern is None:
        return True
    return bool(pattern.search(_clean_text(value)))


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _song_id(title, artist, year):
    return f"{title}#{artist}#{year}"


def _collect_query_items(table, **query_kwargs):
    items = []
    response = table.query(**query_kwargs)
    items.extend(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))

    return items


def _collect_scan_items(table, **scan_kwargs):
    items = []
    response = table.scan(**scan_kwargs)
    items.extend(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))

    return items


def _sign_image(song):
    image_key = song.get("image_key")

    if S3_BUCKET_NAME and image_key:
        try:
            return s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": S3_BUCKET_NAME, "Key": image_key},
                ExpiresIn=PRESIGNED_URL_TTL,
            )
        except (ClientError, NoCredentialsError):
            pass

    return song.get("img_url") or song.get("image_url") or ""


def _serialize_song(song):
    return {
        "song_id": song.get("song_id") or _song_id(song.get("title", ""), song.get("artist", ""), song.get("year", "")),
        "title": song.get("title", ""),
        "artist": song.get("artist", ""),
        "year": song.get("year", ""),
        "album": song.get("album", ""),
        "image_url": _sign_image(song),
        "image_key": song.get("image_key", ""),
    }


def _apply_song_filters(items, title, artist, album, year):
    title_pattern = _build_regex_pattern(title)
    artist_pattern = _build_regex_pattern(artist)
    album_pattern = _build_regex_pattern(album)
    year_pattern = _build_regex_pattern(year)

    filtered = []
    for song in items:
        if not _regex_match(title_pattern, song.get("title")):
            continue
        if not _regex_match(artist_pattern, song.get("artist")):
            continue
        if not _regex_match(album_pattern, song.get("album")):
            continue
        if not _regex_match(year_pattern, song.get("year")):
            continue
        filtered.append(song)

    return filtered


def _fetch_music_candidates(title, artist, album, year):
    if album:
        return _collect_scan_items(music_table)

    if title:
        exact_title = _collect_query_items(music_table, KeyConditionExpression=Key("title").eq(title))
        if exact_title:
            return exact_title

    if artist and year:
        try:
            exact_artist_year = _collect_query_items(
                music_table,
                IndexName="ArtistYearIndex",
                KeyConditionExpression=Key("artist").eq(artist) & Key("year").eq(year),
            )
            if exact_artist_year:
                return exact_artist_year
        except ClientError:
            pass

    if artist:
        try:
            exact_artist = _collect_query_items(
                music_table,
                IndexName="ArtistYearIndex",
                KeyConditionExpression=Key("artist").eq(artist),
            )
            if exact_artist:
                return exact_artist
        except ClientError:
            pass

    if year:
        try:
            exact_year = _collect_query_items(
                music_table,
                IndexName="YearTitleIndex",
                KeyConditionExpression=Key("year").eq(year),
            )
            if exact_year:
                return exact_year
        except ClientError:
            pass

    return _collect_scan_items(music_table)


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/", methods=["GET"])
def index():
    return jsonify(
        {
            "message": "EC2 music subscription API is running",
            "health": "/health",
            "api": ["/api/register", "/api/login", "/api/music", "/api/subscriptions"],
        }
    )


@app.route("/health", methods=["GET"])
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "music-subscription-api-ec2",
            "aws_region": AWS_REGION,
            "tables": {
                "users": USERS_TABLE_NAME,
                "music": MUSIC_TABLE_NAME,
                "subscriptions": SUBSCRIPTIONS_TABLE_NAME,
            },
            "images_bucket": S3_BUCKET_NAME,
        }
    )


@app.errorhandler(NoCredentialsError)
def handle_no_credentials_error(error):
    return (
        jsonify(
            {
                "message": "AWS credentials not found.",
                "hint": "Attach an IAM role to EC2/ECS or configure AWS credentials.",
            }
        ),
        503,
    )


@app.errorhandler(EndpointConnectionError)
def handle_endpoint_connection_error(error):
    return (
        jsonify(
            {
                "message": "Unable to connect to AWS endpoint.",
                "hint": "Confirm region, IAM, networking, and VPC endpoint/NAT settings.",
            }
        ),
        503,
    )


@app.errorhandler(ClientError)
def handle_client_error(error):
    code = error.response.get("Error", {}).get("Code", "ClientError")

    if code == "ResourceNotFoundException":
        return (
            jsonify(
                {
                    "message": "DynamoDB table not found.",
                    "hint": "Run EC2/backend/create_aws_tables.py first on EC2.",
                    "tables": {
                        "users": USERS_TABLE_NAME,
                        "music": MUSIC_TABLE_NAME,
                        "subscriptions": SUBSCRIPTIONS_TABLE_NAME,
                    },
                }
            ),
            503,
        )

    return jsonify({"message": "AWS/DynamoDB request failed.", "code": code}), 500


@app.route("/register", methods=["POST"])
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}

    email = _clean_text(data.get("email"))
    username = _clean_text(data.get("username") or data.get("user_name"))
    password = _clean_text(data.get("password"))

    if not email or not username or not password:
        return jsonify({"message": "email, username and password are required"}), 400

    existing = users_table.get_item(Key={"email": email}).get("Item")
    if existing:
        return jsonify({"message": "The email already exists"}), 409

    users_table.put_item(
        Item={
            "email": email,
            "username": username,
            "user_name": username,
            "password": password,
            "created_at": _now_iso(),
        }
    )

    return jsonify({"message": "User registered"}), 201


@app.route("/login", methods=["POST"])
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}

    email = _clean_text(data.get("email"))
    password = _clean_text(data.get("password"))

    if not email or not password:
        return jsonify({"message": "email and password are required"}), 400

    user = users_table.get_item(Key={"email": email}).get("Item")

    if not user or user.get("password") != password:
        return jsonify({"message": "email or password is invalid"}), 401

    return jsonify(
        {
            "message": "Login success",
            "user": {
                "email": user.get("email"),
                "username": user.get("username") or user.get("user_name"),
            },
        }
    )


@app.route("/music", methods=["GET"])
@app.route("/api/music", methods=["GET"])
def search_music():
    title = _clean_text(request.args.get("title", ""))
    artist = _clean_text(request.args.get("artist", ""))
    album = _clean_text(request.args.get("album", ""))
    year = _clean_text(request.args.get("year", ""))

    if not any([title, artist, album, year]):
        return jsonify({"message": "At least one query field is required", "items": []}), 400

    candidates = _fetch_music_candidates(title, artist, album, year)
    filtered = _apply_song_filters(candidates, title, artist, album, year)

    unique = {}
    for song in filtered:
        sid = song.get("song_id") or _song_id(song.get("title", ""), song.get("artist", ""), song.get("year", ""))
        unique[sid] = _serialize_song(song)

    result = sorted(unique.values(), key=lambda x: (x["title"].lower(), x["artist"].lower(), x["year"]))
    return jsonify(result)


def _load_song_by_identity(title, artist, year):
    response = music_table.get_item(Key={"title": title, "artist_year": f"{artist}#{year}"})
    return response.get("Item")


@app.route("/subscription", methods=["GET", "DELETE"])
@app.route("/api/subscriptions", methods=["GET", "POST", "DELETE"])
@app.route("/subscribe", methods=["POST"])
def subscriptions():
    if request.method == "GET":
        user_email = _clean_text(request.args.get("user") or request.args.get("email"))
        if not user_email:
            return jsonify({"message": "email is required", "items": []}), 400

        items = _collect_query_items(
            subs_table,
            KeyConditionExpression=Key("user_email").eq(user_email),
        )
        songs = [_serialize_song(item) for item in items]
        songs.sort(key=lambda x: (x["title"].lower(), x["artist"].lower(), x["year"]))
        return jsonify(songs)

    data = request.get_json(silent=True) or {}
    user_email = _clean_text(data.get("user") or data.get("user_email") or data.get("email"))

    if not user_email:
        return jsonify({"message": "user_email is required"}), 400

    if request.method == "POST":
        title = _clean_text(data.get("title"))
        artist = _clean_text(data.get("artist"))
        year = _clean_text(data.get("year"))

        if not title or not artist or not year:
            return jsonify({"message": "title, artist and year are required"}), 400

        song = _load_song_by_identity(title, artist, year)
        if not song:
            return jsonify({"message": "Song not found"}), 404

        song_id = song.get("song_id") or _song_id(song["title"], song["artist"], song["year"])

        try:
            subs_table.put_item(
                Item={
                    "user_email": user_email,
                    "song_id": song_id,
                    "title": song.get("title", ""),
                    "artist": song.get("artist", ""),
                    "year": song.get("year", ""),
                    "album": song.get("album", ""),
                    "image_key": song.get("image_key", ""),
                    "img_url": song.get("img_url") or song.get("image_url") or "",
                    "subscribed_at": _now_iso(),
                },
                ConditionExpression="attribute_not_exists(song_id)",
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return jsonify({"message": "Song already subscribed"}), 409
            raise

        return jsonify({"message": "Subscription created", "song_id": song_id}), 201

    song_id = _clean_text(data.get("song_id"))
    if not song_id:
        title = _clean_text(data.get("title"))
        artist = _clean_text(data.get("artist"))
        year = _clean_text(data.get("year"))
        if title and artist and year:
            song_id = _song_id(title, artist, year)

    if not song_id:
        return jsonify({"message": "song_id (or title + artist + year) is required"}), 400

    subs_table.delete_item(Key={"user_email": user_email, "song_id": song_id})
    return jsonify({"message": "Subscription removed"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
