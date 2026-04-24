# Source adaptation: based on existing root-level seed_users.py in this repository.
import os
from datetime import datetime, timezone

import boto3

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL", "").strip()
USERS_TABLE_NAME = os.getenv("USERS_TABLE_NAME", "music_shared_users")

# Required assignment pattern example:
#   email: s3######0@student.rmit.edu.au ... s3######9@student.rmit.edu.au
#   user_name: FirstnameLastname0 ... FirstnameLastname9
#   password: 012345 ... 901234
STUDENT_EMAIL_PREFIX = os.getenv("STUDENT_EMAIL_PREFIX", "s3XXXXXX")
USER_NAME_PREFIX = os.getenv("USER_NAME_PREFIX", "FirstnameLastname")


def _password_for_index(index):
    return "".join(str((index + offset) % 10) for offset in range(6))


def _build_seed_users():
    users = []
    for index in range(10):
        users.append(
            {
                "email": f"{STUDENT_EMAIL_PREFIX}{index}@student.rmit.edu.au",
                "username": f"{USER_NAME_PREFIX}{index}",
                "password": _password_for_index(index),
            }
        )
    return users


def _resource_kwargs():
    kwargs = {"region_name": AWS_REGION}
    if DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL
    return kwargs


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def main():
    dynamodb = boto3.resource("dynamodb", **_resource_kwargs())
    users_table = dynamodb.Table(USERS_TABLE_NAME)

    seed_users = _build_seed_users()

    for user in seed_users:
        users_table.put_item(
            Item={
                "email": user["email"],
                "username": user["username"],
                "user_name": user["username"],
                "password": user["password"],
                "created_at": _now_iso(),
            }
        )

    print(f"Inserted {len(seed_users)} users into {USERS_TABLE_NAME}")
    print(
        "Seed pattern used: "
        f"{STUDENT_EMAIL_PREFIX}0@student.rmit.edu.au .. {STUDENT_EMAIL_PREFIX}9@student.rmit.edu.au"
    )


if __name__ == "__main__":
    main()
