from pathlib import Path
from typing import Literal, Dict, Set, Generator, Optional
import shutil
import os
import threading
from queue import Queue, Empty
from dataclasses import dataclass
import time

from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
    FileSizeColumn,
)
from rich.console import Console
from rich.live import Live
from rich.table import Table

@dataclass
class FileTransferTask:
    """Represents a single file transfer task."""
    source: Path
    relative_path: Path
    size: int

class TransferManager:
    """Manages parallel file transfers between source, jump drive, and destination."""
    
    def __init__(
        self,
        source_path: Path,
        jump_path: Path,
        dest_path: Path,
        num_threads: int = 2,
    ):
        self.source_path = source_path
        self.jump_path = jump_path
        self.dest_path = dest_path
        self.num_threads = num_threads
        
        # Queues for each transfer stage
        self.to_jump_queue: Queue[FileTransferTask] = Queue()
        self.to_dest_queue: Queue[FileTransferTask] = Queue()
        
        # Track progress
        self.total_size = 0
        self.transferred_to_jump = 0
        self.transferred_to_dest = 0
        self.skipped_size = 0
        
        # Track completed files
        self.completed_to_jump: Set[Path] = set()
        self.completed_to_dest: Set[Path] = set()
        self.skipped_files: Set[Path] = set()
        
        # Threads
        self.to_jump_threads: list[threading.Thread] = []
        self.to_dest_threads: list[threading.Thread] = []
        self.scanner_thread: Optional[threading.Thread] = None
        
        # Control flags
        self.stop_threads = False
        self.scan_complete = False
        self.console = Console()
        
        # Track total files for completion check
        self.total_files = 0

    def scan_files(self) -> Generator[FileTransferTask, None, None]:
        """Generator that yields file transfer tasks as they're discovered.
        
        Yields:
            FileTransferTask: Tasks for each file found
        """
        for dirpath, _, filenames in os.walk(self.source_path):
            current_path = Path(dirpath)
            for filename in filenames:
                if self.stop_threads:
                    return
                    
                file_path = current_path / filename
                if not file_path.is_symlink():
                    relative_path = file_path.relative_to(self.source_path)
                    
                    # Check if file already exists at destination
                    dest_path = self.dest_path / relative_path
                    if dest_path.exists():
                        size = file_path.stat().st_size
                        self.skipped_size += size
                        self.skipped_files.add(relative_path)
                        continue
                        
                    size = file_path.stat().st_size
                    self.total_size += size
                    self.total_files += 1
                    
                    yield FileTransferTask(
                        source=file_path,
                        relative_path=relative_path,
                        size=size,
                    )

    def scanner_worker(self) -> None:
        """Worker thread that scans for files and adds them to the queue."""
        self.console.print("[bold blue]Scanning source directory...[/bold blue]")
        
        for task in self.scan_files():
            if self.stop_threads:
                break
            self.to_jump_queue.put(task)
            
        self.scan_complete = True
        self.console.print(
            f"[green]Found {self.total_files} files to transfer "
            f"({len(self.skipped_files)} files already exist at destination)[/green]"
        )

    def transfer_worker(
        self,
        queue: Queue[FileTransferTask],
        base_dest: Path,
        is_jump: bool,
    ) -> None:
        """Worker function for file transfer threads."""
        while not (self.stop_threads or 
                  (self.scan_complete and queue.empty() and
                   ((is_jump and len(self.completed_to_jump) == self.total_files) or
                    (not is_jump and len(self.completed_to_dest) == len(self.completed_to_jump))))):
            try:
                task = queue.get(timeout=0.5)
            except Empty:
                continue

            dest_path = base_dest / task.relative_path
            
            # Skip if file exists at final destination (double-check)
            if not is_jump and dest_path.exists():
                queue.task_done()
                continue
                
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # Determine source path based on stage
            source_path = task.source if is_jump else (self.jump_path / task.relative_path)
            
            # Only proceed if source exists
            if source_path.exists():
                shutil.copy2(source_path, dest_path)
                
                if is_jump:
                    self.transferred_to_jump += task.size
                    self.completed_to_jump.add(task.relative_path)
                    # Add to destination queue once copied to jump
                    self.to_dest_queue.put(task)
                else:
                    self.transferred_to_dest += task.size
                    self.completed_to_dest.add(task.relative_path)

            queue.task_done()

    def create_progress_table(self) -> Table:
        """Create a progress table for display."""
        table = Table()
        table.add_column("Stage")
        table.add_column("Progress")
        table.add_column("Files")
        
        # Calculate percentages
        total_size_with_skipped = self.total_size + self.skipped_size
        jump_percent = (self.transferred_to_jump / self.total_size * 100) if self.total_size > 0 else 0
        dest_percent = (self.transferred_to_dest / self.total_size * 100) if self.total_size > 0 else 0
        
        table.add_row(
            "To Jump Drive",
            f"{jump_percent:.1f}% ({self.transferred_to_jump}/{self.total_size} bytes)",
            f"{len(self.completed_to_jump)}/{self.total_files} files"
        )
        table.add_row(
            "To Destination",
            f"{dest_percent:.1f}% ({self.transferred_to_dest}/{self.total_size} bytes)",
            f"{len(self.completed_to_dest)}/{self.total_files} files"
        )
        if self.skipped_files:
            table.add_row(
                "Skipped (Already Exist)",
                f"100% ({self.skipped_size} bytes)",
                f"{len(self.skipped_files)} files"
            )
        
        return table

    def start_transfer(self) -> None:
        """Start the parallel transfer process."""
        # Create destination directories
        self.jump_path.mkdir(parents=True, exist_ok=True)
        self.dest_path.mkdir(parents=True, exist_ok=True)
        
        # Start scanner thread
        self.scanner_thread = threading.Thread(target=self.scanner_worker)
        self.scanner_thread.daemon = True
        self.scanner_thread.start()
        
        # Start transfer threads
        for _ in range(self.num_threads):
            # To jump drive threads
            thread = threading.Thread(
                target=self.transfer_worker,
                args=(self.to_jump_queue, self.jump_path, True)
            )
            thread.daemon = True
            thread.start()
            self.to_jump_threads.append(thread)
            
            # To destination threads
            thread = threading.Thread(
                target=self.transfer_worker,
                args=(self.to_dest_queue, self.dest_path, False)
            )
            thread.daemon = True
            thread.start()
            self.to_dest_threads.append(thread)

        try:
            # Display progress
            with Live(self.create_progress_table(), refresh_per_second=4) as live:
                while any(thread.is_alive() for thread in [self.scanner_thread] + self.to_jump_threads + self.to_dest_threads):
                    live.update(self.create_progress_table())
                    time.sleep(0.25)
                    
                    # Extra completion check
                    if (self.scan_complete and
                        len(self.completed_to_jump) == self.total_files and 
                        len(self.completed_to_dest) == self.total_files):
                        break
                    
        except KeyboardInterrupt:
            self.stop_threads = True
            raise
        
        finally:
            # Wait for threads to finish
            for thread in [self.scanner_thread] + self.to_jump_threads + self.to_dest_threads:
                thread.join(timeout=1.0)

        self.console.print("\n[bold green]Transfer complete! ✨[/bold green]")

def start_jump_transfer(
    external_source: str,
    jump_drive: str,
    external_dest: str,
) -> None:
    """Main function to handle the parallel file transfer process.
    
    Args:
        external_source (str): Path to the source external drive
        jump_drive (str): Path to the jump drive
        external_dest (str): Path to the destination external drive
    """
    source_path = Path(external_source)
    jump_path = Path(jump_drive)
    dest_path = Path(external_dest)
    
    if not source_path.exists():
        raise FileNotFoundError(f"Source path does not exist: {source_path}")
    
    manager = TransferManager(
        source_path=source_path,
        jump_path=jump_path,
        dest_path=dest_path,
        # Use 1 thread per transfer direction for balance
        num_threads=1,
    )
    
    # Start parallel transfer
    manager.start_transfer()
