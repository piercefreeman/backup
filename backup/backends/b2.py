from boto3 import client, resource
from botocore.config import Config
from botocore.exceptions import ClientError

from backup.backends.base import BaseBackend


class B2Backend(BaseBackend):
    def __init__(
        self,
        endpoint: str,
        key_id: str,
        application_key: str,
        bucket_name: str,
    ):
        """
        :param endpoint: Backblaze endpoint, eg. `s3.us-east-005.backblazeb2.com`. This is located in the B2 console
            under the bucket definition.
        :param key_id: Backblaze keyID. This is located in the B2 console under the bucket definition.
        :param application_key: Backblaze applicationKey. This is located in the B2 console under the bucket definition.
        :param bucket_name: The name of the bucket to use.

        """
        self.b2_client = client(
            service_name="s3",
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=application_key
        )
        self.b2_resource = resource(
            service_name='s3',
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=application_key,
            config=Config(
                signature_version='s3v4',
            )
        )
        self.bucket_name = bucket_name

    def exists(self, path: str):
        try:
            self.b2_client.head_object(Bucket=self.bucket_name, Key=path)
            return True
        except ClientError:
            return False

    def write_file(self, path: str, data: bytes):
        self.b2_resource.Object(self.bucket_name, path).put(Body=data)
