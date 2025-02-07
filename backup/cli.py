from click import group, option, confirm
from backup.icloud import ICloudPhotosDownloader
from backup.config import Settings, BackupBackend
from dotenv import load_dotenv
from backup.backends.local import LocalBackend
from backup.backends.b2 import B2Backend
from asyncio import run
from backup.jump import start_jump_transfer
from pathlib import Path
from typing import Optional
from rich import print as rprint

# FIGLET font: Standard
MAIN_LOGO = """
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
@option(
    "--progress-path",
    required=False,
    help="Path to save/load discovered files",
    default=None,
)
@option(
    "--max-queue-size",
    required=False,
    help="Maximum number of files to queue at once",
    default=1000,
)
def jump_transfer(
    source: str,
    jump: str,
    dest: str,
    max_queue_size: int,
    progress_path: Optional[str] = None,
):
    """Transfer files from source to destination using a jump drive."""
    if progress_path:
        progress_path = Path(progress_path)
        discovered_file = progress_path / "discovered.jsonl"
        transferred_file = progress_path / "transferred.jsonl"

        # Check if either file exists
        if discovered_file.exists() or transferred_file.exists():
            rprint(
                f"[yellow]⚠️  Warning: Progress files already exist at {progress_path}[/yellow]"
            )
            rprint(
                "[yellow]This could indicate an interrupted transfer or a different transfer using the same path.[/yellow]"
            )

            if not confirm(
                "Do you want to continue and append to the existing progress files?",
                default=False,
            ):
                rprint("[yellow]Transfer cancelled by user.[/yellow]")
                return

            rprint("[green]Continuing with existing progress files...[/green]")

    start_jump_transfer(source, jump, dest, max_queue_size, progress_path)
