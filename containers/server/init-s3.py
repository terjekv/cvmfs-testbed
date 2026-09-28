import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

client = boto3.client(
    "s3",
    endpoint_url="http://object-store:8333",
    region_name="us-east-1",
    aws_access_key_id="testbed",
    aws_secret_access_key="testbed-only-secret",
    config=Config(
        s3={"addressing_style": "path"},
        connect_timeout=2,
        read_timeout=3,
        retries={"max_attempts": 0},
    ),
)
deadline = time.monotonic() + 90
while True:
    try:
        client.create_bucket(Bucket="cvmfs")
        break
    except ClientError as error:
        if error.response["Error"]["Code"] in {
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        }:
            break
        if time.monotonic() >= deadline:
            raise
    except BotoCoreError:
        if time.monotonic() >= deadline:
            raise
    time.sleep(1)
print("S3 bucket is ready", flush=True)
