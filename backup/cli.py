from click import command
from backup.icloud import ICloudPhotosDownloader
from backup.config import Settings, BackupBackend
from dotenv import load_dotenv
from backup.backends.local import LocalBackend
from backup.backends.b2 import B2Backend
from asyncio import run

# FIGLET font: Standard
MAIN_LOGO = (    
"""
                    ___           ___           ___           ___           ___   
     _____         /  /\         /  /\         /__/|         /__/\         /  /\  
    /  /::\       /  /::\       /  /:/        |  |:|         \  \:\       /  /::\ 
   /  /:/\:\     /  /:/\:\     /  /:/         |  |:|          \  \:\     /  /:/\:\ 
  /  /:/~/::\   /  /:/~/::\   /  /:/  ___   __|  |:|      ___  \  \:\   /  /:/~/:/
 /__/:/ /:/\:| /__/:/ /:/\:\ /__/:/  /  /\ /__/\_|:|____ /__/\  \__\:\ /__/:/ /:/ 
 \  \:\/:/~/:/ \  \:\/:/__\/ \  \:\ /  /:/ \  \:\/:::::/ \  \:\ /  /:/ \  \:\/:/  
  \  \::/ /:/   \  \::/       \  \:\  /:/   \  \::/~~~~   \  \:\  /:/   \  \::/   
   \  \:\/:/     \  \:\        \  \:\/:/     \  \:\        \  \:\/:/     \  \:\   
    \  \::/       \  \:\        \  \::/       \  \:\        \  \::/       \  \:\  
     \__\/         \__\/         \__\/         \__\/         \__\/         \__\/  
"""
)

@command()
def main():
    print(MAIN_LOGO)

    load_dotenv()

    config = Settings()

    if config.backup_backend == BackupBackend.LOCAL:
        backend = LocalBackend()
    elif config.backup_backend == BackupBackend.B2:
        backend = B2Backend(
            endpoint=config.b2_endpoint,
            key_id=config.b2_key_id,
            application_key=config.b2_application_key,
            bucket_name=config.b2_bucket_name,
        )
    else:
        raise NotImplementedError(f"Backend {config.backup_backend} is not implemented")

    print("Syncing iCloud Photos...")
    icloud_photos = ICloudPhotosDownloader(
        username=config.icloud_photos_username,
        password=None,
        backend=backend,
    )
    run(icloud_photos.sync())
