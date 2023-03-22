import os
from dataclasses import dataclass
from datetime import datetime
from logging import debug, error, info
from tempfile import TemporaryDirectory
from typing import Any

from icloudpd import download, exif_datetime
from icloudpd.authentication import TwoStepAuthRequiredError, authenticator
from icloudpd.logger import setup_logger
from icloudpd.paths import clean_filename
from pyicloud_ipd.exceptions import PyiCloudAPIResponseError
from tqdm import tqdm
from tzlocal import get_localzone

from backup.backends.base import BaseBackend

DEFAULT_ALBUM = "All Photos"
PHOTO_SIZE = "original"
FOLDER_STRUCTURE = "{:%Y/%m/%d}"


@dataclass
class PhotoContext:
    photo: Any
    created_date: datetime
    remote_path: str


class ICloudPhotosDownloader:
    """
    Download logic for iCloud Photos. Majority of logic is forked from the icloudpd CLI, dropping
    support for legacy Python versions and supporting arbitrary backends.
    https://github.com/icloud-photos-downloader/icloud_photos_downloader/blob/master/icloudpd/base.py#L288

    """
    def __init__(self, username: str, password: str, backend: BaseBackend):
        self.username = username
        self.password = password
        self.backend = backend

    def sync(self):
        icloud = self.icloud_login()
        photos = self.get_core_images(icloud, DEFAULT_ALBUM)
        for photo in tqdm(self.iter_photos(photos), total=len(photos)):
            # Check if this photo has already been added to the remote
            # TODO: Add checksums here
            if self.backend.exists(photo.remote_path):
                continue

            self.sync_photo(icloud, photo)

    def get_core_images(self, icloud, album_name: str):
        try:
            return icloud.photos.albums[album_name]
        except PyiCloudAPIResponseError as err:
            # For later: come up with a nicer message to the user. For now take the
            # exception text
            raise err

    def iter_photos(self, photos):
        
        for photo in photos:
            filename = clean_filename(photo.filename)

            if photo.item_type not in {"image", "movie"}:
                info(
                    f"Skipping {filename}, only downloading photos and videos. "
                    f"(Item type was: {photo.item_type})"
                )
                continue

            try:
                created_date = photo.created.astimezone(get_localzone())
            except (ValueError, OSError):
                error(f"Could not convert photo created date to local timezone ({photo.created})")
                created_date = photo.created

            # The remote path should be prefixed with the date
            date_path = FOLDER_STRUCTURE.format(created_date)

            yield PhotoContext(
                photo=photo,
                created_date=created_date,
                remote_path=os.path.join(date_path, filename)
            )

    def sync_photo(self, icloud, photo_payload: PhotoContext):
        with TemporaryDirectory() as download_root:
            download_path = os.path.join(download_root, photo_payload.photo.filename)

            download_result = download.download_media(
                icloud,
                photo_payload.photo,
                download_path,
                PHOTO_SIZE
            )

            self.inject_exif(photo_payload.photo, download_result, download_path, photo_payload.created_date)

            # Write this file to remote
            with open(download_path, "rb") as file:
                self.backend.write_file(photo_payload.remote_path, file.read())

    def inject_exif(
            self,
            photo,
            download_result,
            download_path: str,
            created_date: datetime,
            set_exif_datetime: bool = True
        ):
        if not download_result:
            return

        if all([
            set_exif_datetime,
            (
                clean_filename(photo.filename)
                .lower()
                .endswith((".jpg", ".jpeg"))
            ),
            not exif_datetime.get_photo_exif(download_path)
        ]):
            # %Y:%m:%d looks wrong, but it's the correct format
            date_str = created_date.strftime("%Y-%m-%d %H:%M:%S%z")
            debug(
                "Setting EXIF timestamp for %s: %s",
                download_path,
                date_str,
            )
            exif_datetime.set_photo_exif(
                download_path,
                created_date.strftime("%Y:%m:%d %H:%M:%S"),
            )
        download.set_utime(download_path, created_date)

    def icloud_login(self, cookie_directory="~/.pyicloud"):
        try:
            return authenticator("com")(
                self.username,
                self.password,
                cookie_directory,
                raise_error_on_2sa=False,
                client_id=os.environ.get("CLIENT_ID"),
            )
        except TwoStepAuthRequiredError:
            print("Two-step authentication required....")
            return None
