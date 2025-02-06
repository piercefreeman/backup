from click import command, group, option
from backup.icloud import ICloudPhotosDownloader
from backup.config import Settings, BackupBackend
from dotenv import load_dotenv
from backup.backends.local import LocalBackend
from backup.backends.b2 import B2Backend
from asyncio import run
from click import group
from backup.jump import start_jump_transfer

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

@group()
def main():
    pass

@main.command()
def sync_icloud_photos():
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

@main.command()
@option("--source", required=True, help="Path to source external drive")
@option("--jump", required=True, help="Path to jump drive")
@option("--dest", required=True, help="Path to destination external drive")
def jump_transfer(source: str, jump: str, dest: str):
    start_jump_transfer(source, jump, dest)
