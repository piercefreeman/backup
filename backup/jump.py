from pathlib import Path
from typing import Literal, Dict, Set, Generator, Optional
import shutil
import os
import threading
from queue import Queue, Empty
from dataclasses import dataclass, asdict
import time
from re import compile as re_compile
import json
from datetime import datetime

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
from rich import print as rprint
from rich.panel import Panel

@dataclass
class FileTransferTask:
    """Represents a single file transfer task."""
    source: Path
    relative_path: Path
    size: int

@dataclass
class DiscoveredFile:
    """Represents a discovered file and its metadata."""
    relative_path: str
    size: int
    last_modified: float
    discovered_at: float

@dataclass
class AlreadyTransferredFile:
    """Represents a file that has already been transferred."""
    relative_path: str
    transferred_at: float

# For now skip patterns just work at the filepath.name level
SKIP_PATTERNS = [
    r"\.DS_Store$",
    r".*\.app$",
    r".*\.dmg$",
    r".*\.pkg$",
    # Personal
    r"^Adobe Archive$",
    r"^WindowsSupport$",
    r"^NCSEXPER$",
    r"^Applications$",
    r"^Library$",
]

class TransferManager:
    """Manages parallel file transfers between source, jump drive, and destination."""
    
    def __init__(
        self,
        source_path: Path,
        jump_path: Path,
        dest_path: Path,
        progress_path: Optional[Path] = None,
        num_threads: int = 2,
    ):
        if not source_path.exists():
            raise FileNotFoundError(f"Source path does not exist: {source_path}")
            
        self.source_path = source_path
        self.jump_path = jump_path
        self.dest_path = dest_path
        self.progress_path = progress_path
        self.discovered_path = progress_path / "discovered.jsonl" if progress_path else None
        self.transferred_path = progress_path / "transferred.jsonl" if progress_path else None
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
        
        # Track current files being processed
        self.current_jump_files: Set[Path] = set()
        self.current_dest_files: Set[Path] = set()
        
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
        self.files_found = 0
        
        # Lock for updating current files
        self.current_files_lock = threading.Lock()
        
        self.skip_regexes = [re_compile(pattern) for pattern in SKIP_PATTERNS]

        # Load already transferred files
        self.transferred_files: Dict[str, AlreadyTransferredFile] = {}
        if self.transferred_path and self.transferred_path.exists():
            with self.transferred_path.open("r") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        transferred = AlreadyTransferredFile(**data)
                        self.transferred_files[transferred.relative_path] = transferred
                    except (json.JSONDecodeError, KeyError):
                        continue
            rprint(f"[blue]📚 Loaded {len(self.transferred_files)} previously transferred files[/blue]")

        rprint(Panel(f"[blue]Starting transfer process[/blue]\nSource: {source_path}\nJump Drive: {jump_path}\nDestination: {dest_path}"))

    def scan_files(self) -> Generator[FileTransferTask, None, None]:
        """Generator that yields file transfer tasks as they're discovered."""
        rprint("[blue]🔍 Starting file scan...[/blue]")
        last_status_time = time.time()

        for file_path in self.iterate_known_paths():
            if self.stop_threads:
                rprint("[yellow]⚠️  Scan interrupted[/yellow]")
                return

            # Convert to Path if it's a string (from discovered files)
            if isinstance(file_path, str):
                file_path = self.source_path / file_path
                
            # Skip shortcuts, only copy underlying files
            try:
                if file_path.is_symlink():
                    continue
            except OSError as e:
                rprint(f"[dim yellow]⏩ Skipping {file_path} {e} (os error)[/dim yellow]")
                continue

            try:
                relative_path = file_path.relative_to(self.source_path)
            except ValueError:
                rprint(f"[dim yellow]⏩ Skipping {file_path} (not relative to source)[/dim yellow]")
                continue
            
            if self.should_skip(file_path):
                rprint(f"[dim yellow]⏩ Skipping {relative_path} (in skip list)[/dim yellow]")
                continue

            # Check if file has already been transferred - if so then we don't need any disk io
            relative_str = str(relative_path)
            if relative_str in self.transferred_files:
                size = file_path.stat().st_size
                self.skipped_size += size
                self.skipped_files.add(relative_path)
                rprint(f"[dim yellow]⏩ Skipping {relative_path} (already transferred)[/dim yellow]")
                continue

            # Check if file still exists
            if not file_path.exists(): 
                rprint(f"[dim yellow]⏩ Skipping {relative_path} (no longer exists)[/dim yellow]")
                continue

            # Check if file already exists at final destination
            dest_path = self.dest_path / relative_path
            if dest_path.exists():
                size = file_path.stat().st_size
                self.skipped_size += size
                self.skipped_files.add(relative_path)   
                self.save_transferred_file(str(relative_path))
                rprint(f"[dim yellow]⏩ Skipping {relative_path} (already at destination)[/dim yellow]")
                continue
                
            size = file_path.stat().st_size
            self.total_size += size
            self.total_files += 1
            self.files_found += 1

            # Show status every second
            current_time = time.time()
            if current_time - last_status_time > 1:
                rprint(f"[dim]📁 Found {self.files_found} files ({self.total_size / 1024 / 1024:.1f} MB total)[/dim]")
                last_status_time = current_time
            
            yield FileTransferTask(
                source=file_path,
                relative_path=relative_path,
                size=size,
            )
    
    def iterate_known_paths(self) -> Generator[Path | str, None, None]:
        """Iterate over both discovered and newly found paths.
        
        This is a two-phase generator:
        1. First yields all previously discovered paths from the discovery file
        2. Then walks the filesystem to find new paths, saving them as discovered
        """
        # Load discovered files if they exist
        self.discovered_files: Dict[str, DiscoveredFile] = {}
        discovered_paths = set()

        if self.discovered_path and self.discovered_path.exists():
            with self.discovered_path.open("r") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        discovered = DiscoveredFile(**data)
                        self.discovered_files[discovered.relative_path] = discovered
                        discovered_paths.add(discovered.relative_path)
                    except (json.JSONDecodeError, KeyError):
                        continue
            
            rprint(f"[blue]📚 Loaded {len(self.discovered_files)} previously discovered files[/blue]")

            # First yield all discovered files that still exist
            for relative_path in discovered_paths:
                file_path = self.source_path / relative_path
                if file_path.exists():
                    yield relative_path

        # We might need to create the parent directory if it doesn't exist
        if self.discovered_path:
            self.discovered_path.parent.mkdir(parents=True, exist_ok=True)
            rprint(f"[blue]📚 Caching discovered files to {self.discovered_path}[/blue]")

        # Now walk the filesystem to find new paths
        for dirpath, dirnames, filenames in os.walk(self.source_path):
            current_path = Path(dirpath)
            relative_path = current_path.relative_to(self.source_path)

            # Check if the current directory should be skipped
            # If it should be skipped, remove all subdirs from dirnames to prevent descent
            if str(relative_path) != "." and self.should_skip(current_path):
                rprint(f"[dim yellow]⏩ Skipping directory {relative_path} and its contents (in skip list)[/dim yellow]")
                dirnames.clear()  # This prevents os.walk from descending into this directory
                continue
        
            for filename in filenames:
                file_path = current_path / filename
                relative_str = str(file_path.relative_to(self.source_path))

                # Skip if we've already seen this file
                if relative_str in discovered_paths:
                    continue

                # Save this discovered file
                try:
                    size = file_path.stat().st_size
                    self.save_discovered_file(file_path, size)
                    yield file_path
                except (OSError, FileNotFoundError):
                    continue

    def scanner_worker(self) -> None:
        """Worker thread that scans for files and adds them to the queue."""
        for task in self.scan_files():
            if self.stop_threads:
                break
            self.to_jump_queue.put(task)
            
        self.scan_complete = True
        rprint(f"\n[green]✨ Scan complete![/green]")
        rprint(Panel.fit(
            "[green]Found {self.total_files} files to transfer[/green]\n"
            f"Total size: {self.total_size / 1024 / 1024:.1f} MB\n"
            f"Skipped {len(self.skipped_files)} existing files "
            f"({self.skipped_size / 1024 / 1024:.1f} MB)"
        ))

    def cleanup_jump_file(self, relative_path: Path) -> None:
        """Clean up a file from the jump drive after successful transfer.
        
        Args:
            relative_path (Path): Relative path of the file to clean up
        """
        jump_file = self.jump_path / relative_path
        try:
            jump_file.unlink()
            # Remove empty parent directories
            current_dir = jump_file.parent
            while current_dir != self.jump_path:
                try:
                    current_dir.rmdir()
                    current_dir = current_dir.parent
                except OSError:
                    # Directory not empty or already removed
                    break
        except Exception as e:
            rprint(f"[yellow]⚠️  Could not remove file from jump drive: {relative_path} ({str(e)})[/yellow]")

    def transfer_worker(
        self,
        queue: Queue[FileTransferTask],
        base_dest: Path,
        is_jump: bool,
    ) -> None:
        """Worker function for file transfer threads."""
        stage = "jump drive" if is_jump else "destination"
        thread_name = threading.current_thread().name
        
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

            # Update current file being processed
            with self.current_files_lock:
                if is_jump:
                    self.current_jump_files.add(task.relative_path)
                else:
                    self.current_dest_files.add(task.relative_path)
            rprint(f"[dim]🔍 Transferring {task.relative_path} to {stage}[/dim]")

            try:
                # Determine source path based on stage
                source_path = task.source if is_jump else (self.jump_path / task.relative_path)
                
                # Only proceed if source exists
                if source_path.exists():
                    try:
                        shutil.copy2(source_path, dest_path)
                        rprint(f"[green]✅ Transferred {task.relative_path} to {stage}[/green]")
                        
                        if is_jump:
                            self.transferred_to_jump += task.size
                            self.completed_to_jump.add(task.relative_path)
                            # Add to destination queue once copied to jump
                            self.to_dest_queue.put(task)
                        else:
                            self.transferred_to_dest += task.size
                            self.completed_to_dest.add(task.relative_path)
                            # Save as transferred and clean up from jump drive after successful transfer
                            self.save_transferred_file(str(task.relative_path))
                            self.cleanup_jump_file(task.relative_path)
                    except Exception as e:
                        rprint(f"[red]❌ Error copying {task.relative_path}: {str(e)}[/red]")
                else:
                    rprint(f"[yellow]⚠️  Source file not found: {source_path}[/yellow]")
            finally:
                # Remove from current files when done
                with self.current_files_lock:
                    if is_jump:
                        self.current_jump_files.discard(task.relative_path)
                    else:
                        self.current_dest_files.discard(task.relative_path)

            queue.task_done()

    def create_progress_table(self) -> Table:
        """Create a progress table for display."""
        table = Table()
        table.add_column("Stage")
        table.add_column("Progress")
        table.add_column("Files")
        table.add_column("Queue Size")
        table.add_column("Space Used")
        table.add_column("Current Files")
        
        # Calculate percentages and space usage
        total_size_with_skipped = self.total_size + self.skipped_size
        jump_percent = (self.transferred_to_jump / self.total_size * 100) if self.total_size > 0 else 0
        dest_percent = (self.transferred_to_dest / self.total_size * 100) if self.total_size > 0 else 0
        
        # Calculate actual space used on jump drive
        # We can use transferred_to_dest as the amount cleaned up since we delete files after successful transfer
        jump_space_used = self.transferred_to_jump - self.transferred_to_dest
        
        # Format current files for display
        with self.current_files_lock:
            jump_files = ", ".join(str(p) for p in self.current_jump_files) or "None"
            dest_files = ", ".join(str(p) for p in self.current_dest_files) or "None"
            
            # Truncate if too long
            max_length = 50
            if len(jump_files) > max_length:
                jump_files = jump_files[:max_length] + "..."
            if len(dest_files) > max_length:
                dest_files = dest_files[:max_length] + "..."
        
        table.add_row(
            "[cyan]To Jump Drive[/cyan]",
            f"{jump_percent:.1f}% ({self.transferred_to_jump / 1024 / 1024:.1f}/{self.total_size / 1024 / 1024:.1f} MB)",
            f"{len(self.completed_to_jump)}/{self.total_files} files",
            f"Queue: {self.to_jump_queue.qsize()}",
            f"Using: {jump_space_used / 1024 / 1024:.1f} MB",
            f"[dim]{jump_files}[/dim]"
        )
        table.add_row(
            "[cyan]To Destination[/cyan]",
            f"{dest_percent:.1f}% ({self.transferred_to_dest / 1024 / 1024:.1f}/{self.total_size / 1024 / 1024:.1f} MB)",
            f"{len(self.completed_to_dest)}/{self.total_files} files",
            f"Queue: {self.to_dest_queue.qsize()}",
            "",
            f"[dim]{dest_files}[/dim]"
        )
        if self.skipped_files:
            table.add_row(
                "[yellow]Skipped[/yellow]",
                f"100% ({self.skipped_size / 1024 / 1024:.1f} MB)",
                f"{len(self.skipped_files)} files",
                "",
                "",
                ""
            )
        
        return table

    def start_transfer(self) -> None:
        """Start the parallel transfer process."""
        # Create destination directories
        self.jump_path.mkdir(parents=True, exist_ok=True)
        self.dest_path.mkdir(parents=True, exist_ok=True)
        
        # Start scanner thread
        self.scanner_thread = threading.Thread(target=self.scanner_worker, name="Scanner")
        self.scanner_thread.daemon = True
        self.scanner_thread.start()
        
        # Start transfer threads
        for i in range(self.num_threads):
            # To jump drive threads
            thread = threading.Thread(
                target=self.transfer_worker,
                args=(self.to_jump_queue, self.jump_path, True),
                name=f"JumpWorker-{i}"
            )
            thread.daemon = True
            thread.start()
            self.to_jump_threads.append(thread)
            
            # To destination threads
            thread = threading.Thread(
                target=self.transfer_worker,
                args=(self.to_dest_queue, self.dest_path, False),
                name=f"DestWorker-{i}"
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
            rprint("\n[yellow]⚠️  Transfer interrupted by user[/yellow]")
            self.stop_threads = True
            raise
        
        finally:
            # Wait for threads to finish
            for thread in [self.scanner_thread] + self.to_jump_threads + self.to_dest_threads:
                thread.join(timeout=1.0)

        rprint(Panel.fit(
            "[bold green]✨ Transfer complete![/bold green]\n"
            f"Transferred: {self.total_files} files ({self.total_size / 1024 / 1024:.1f} MB)\n"
            f"Skipped: {len(self.skipped_files)} files ({self.skipped_size / 1024 / 1024:.1f} MB)"
        ))
    
    def should_skip(self, file_path: Path) -> bool:
        """Determine if a file should be skipped based on patterns."""
        # Right now we assume this list is small enough to iterate over for every filepath, if we start
        # adding automatically generated patterns we'll need to build out a treelike iterator

        # Independently check each level of the filepath
        if any(regex.match(file_path.name) for regex in self.skip_regexes):
            return True

        return False

    def save_discovered_file(self, file_path: Path, size: int) -> None:
        """Save a discovered file to the discovered_path if it exists."""
        if not self.discovered_path:
            return

        relative_path = str(file_path.relative_to(self.source_path))
        discovered = DiscoveredFile(
            relative_path=relative_path,
            size=size,
            last_modified=file_path.stat().st_mtime,
            discovered_at=time.time(),
        )

        # Only write if we haven't seen this file before
        if relative_path not in self.discovered_files:
            self.discovered_files[relative_path] = discovered
            # Append to the file
            with open(self.discovered_path, "a") as f:
                f.write(json.dumps(asdict(discovered)) + "\n")

    def save_transferred_file(self, relative_path: str) -> None:
        """Save a transferred file to the transferred_path if it exists."""
        if not self.transferred_path:
            return

        transferred = AlreadyTransferredFile(
            relative_path=relative_path,
            transferred_at=time.time(),
        )

        # Only write if we haven't seen this file before
        if relative_path not in self.transferred_files:
            self.transferred_files[relative_path] = transferred
            # Append to the file
            with open(self.transferred_path, "a") as f:
                f.write(json.dumps(asdict(transferred)) + "\n")

def start_jump_transfer(
    external_source: str,
    jump_drive: str,
    external_dest: str,
    progress: Optional[str] = None,
) -> None:
    """Main function to handle the parallel file transfer process.
    
    Args:
        external_source (str): Path to the source external drive
        jump_drive (str): Path to the jump drive
        external_dest (str): Path to the destination external drive
        progress_path (Optional[str]): Path to save/load discovered files
    """
    source_path = Path(external_source)
    jump_path = Path(jump_drive)
    dest_path = Path(external_dest)
    progress_path = Path(progress) if progress else None
    
    if not source_path.exists():
        raise FileNotFoundError(f"Source path does not exist: {source_path}")
    
    manager = TransferManager(
        source_path=source_path,
        jump_path=jump_path,
        dest_path=dest_path,
        progress_path=progress_path,
        # Use 1 thread per transfer direction for balance
        num_threads=1,
    )
    
    # Start parallel transfer
    manager.start_transfer()
