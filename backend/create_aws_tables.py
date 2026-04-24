# Source adaptation: based on existing root-level create_tables_local.py in this repository.
import os
import time

import boto3
from botocore.exceptions import ClientError

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL", "").strip()
USERS_TABLE_NAME = os.getenv("USERS_TABLE_NAME", "music_shared_users")
MUSIC_TABLE_NAME = os.getenv("MUSIC_TABLE_NAME", "music_shared_songs")
SUBSCRIPTIONS_TABLE_NAME = os.getenv("SUBSCRIPTIONS_TABLE_NAME", "music_shared_subscriptions")


def _client_kwargs():
    kwargs = {"region_name": AWS_REGION}
    if DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL
    return kwargs


dynamodb = boto3.client("dynamodb", **_client_kwargs())


def _table_exists(table_name):
    try:
        dynamodb.describe_table(TableName=table_name)
        return True
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return False
        raise


def _wait_for_active(table_name):
    while True:
        status = dynamodb.describe_table(TableName=table_name)["Table"]["TableStatus"]
        if status == "ACTIVE":
            return
        time.sleep(2)


def _create_users_table():
    if _table_exists(USERS_TABLE_NAME):
        print(f"{USERS_TABLE_NAME} already exists")
        return

    dynamodb.create_table(
        TableName=USERS_TABLE_NAME,
        KeySchema=[
            {"AttributeName": "email", "KeyType": "HASH"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "email", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    _wait_for_active(USERS_TABLE_NAME)
    print(f"{USERS_TABLE_NAME} created")


def _create_music_table():
    if _table_exists(MUSIC_TABLE_NAME):
        print(f"{MUSIC_TABLE_NAME} already exists")
        return

    dynamodb.create_table(
        TableName=MUSIC_TABLE_NAME,
        KeySchema=[
            {"AttributeName": "title", "KeyType": "HASH"},
            {"AttributeName": "artist_year", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "title", "AttributeType": "S"},
            {"AttributeName": "artist_year", "AttributeType": "S"},
            {"AttributeName": "album", "AttributeType": "S"},
            {"AttributeName": "artist", "AttributeType": "S"},
            {"AttributeName": "year", "AttributeType": "S"},
        ],
        LocalSecondaryIndexes=[
            {
                # LSI supports title + album access pattern within the same partition key (title).
                "IndexName": "TitleAlbumIndex",
                "KeySchema": [
                    {"AttributeName": "title", "KeyType": "HASH"},
                    {"AttributeName": "album", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        GlobalSecondaryIndexes=[
            {
                # GSI supports artist/year query path used by API search endpoints.
                "IndexName": "ArtistYearIndex",
                "KeySchema": [
                    {"AttributeName": "artist", "KeyType": "HASH"},
                    {"AttributeName": "year", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "YearTitleIndex",
                "KeySchema": [
                    {"AttributeName": "year", "KeyType": "HASH"},
                    {"AttributeName": "title", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    _wait_for_active(MUSIC_TABLE_NAME)
    print(f"{MUSIC_TABLE_NAME} created")


def _create_subscriptions_table():
    if _table_exists(SUBSCRIPTIONS_TABLE_NAME):
        print(f"{SUBSCRIPTIONS_TABLE_NAME} already exists")
        return

    dynamodb.create_table(
        TableName=SUBSCRIPTIONS_TABLE_NAME,
        KeySchema=[
            {"AttributeName": "user_email", "KeyType": "HASH"},
            {"AttributeName": "song_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_email", "AttributeType": "S"},
            {"AttributeName": "song_id", "AttributeType": "S"},
            {"AttributeName": "subscribed_at", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "SongSubscribersIndex",
                "KeySchema": [
                    {"AttributeName": "song_id", "KeyType": "HASH"},
                    {"AttributeName": "subscribed_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    _wait_for_active(SUBSCRIPTIONS_TABLE_NAME)
    print(f"{SUBSCRIPTIONS_TABLE_NAME} created")


if __name__ == "__main__":
    _create_users_table()
    _create_music_table()
    _create_subscriptions_table()
    print("All shared DynamoDB tables are ready.")
