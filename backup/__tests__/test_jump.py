import pytest
from pathlib import Path
import os
import shutil
import tempfile
import time
from typing import Generator, Tuple

from backup.jump import TransferManager, FileTransferTask

@pytest.fixture
def test_file_structure() -> Generator[Tuple[Path, dict[str, int]], None, None]:
    """Create a temporary directory with test files and return the path and file sizes.
    
    Returns:
        Tuple of (temp_dir, file_sizes) where file_sizes is a dict of relative paths to sizes
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        root_path = Path(temp_dir)
        
        # Create a nested structure of test files with known content
        file_sizes = {}
        
        # Create some files in root
        for i in range(3):
            file_path = root_path / f"file_{i}.txt"
            content = f"Content for file {i}\n" * (i + 1)
            file_path.write_text(content)
            file_sizes[f"file_{i}.txt"] = len(content.encode())
        
        # Create nested directory structure
        nested_dir = root_path / "nested" / "subdirectory"
        nested_dir.mkdir(parents=True)
        
        for i in range(2):
            file_path = nested_dir / f"nested_file_{i}.txt"
            content = f"Nested content for file {i}\n" * (i + 2)
            file_path.write_text(content)
            file_sizes[str(Path("nested/subdirectory") / f"nested_file_{i}.txt")] = len(content.encode())
        
        yield root_path, file_sizes

@pytest.fixture
def transfer_paths() -> Generator[Tuple[Path, Path, Path], None, None]:
    """Create temporary directories for source, jump, and destination.
    
    Returns:
        Tuple of (source_dir, jump_dir, dest_dir)
    """
    with tempfile.TemporaryDirectory() as source_dir, \
         tempfile.TemporaryDirectory() as jump_dir, \
         tempfile.TemporaryDirectory() as dest_dir:
        yield Path(source_dir), Path(jump_dir), Path(dest_dir)

def test_deep_scan(test_file_structure: Tuple[Path, dict[str, int]], transfer_paths: Tuple[Path, Path, Path]):
    """Test that deep scanning correctly identifies all files."""
    source_path, file_sizes = test_file_structure
    _, jump_path, dest_path = transfer_paths
    
    # Copy test files to source directory
    for item in source_path.glob("**/*"):
        if item.is_file():
            relative_path = item.relative_to(source_path)
            dest_item = transfer_paths[0] / relative_path
            dest_item.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_item)
    
    manager = TransferManager(
        source_path=transfer_paths[0],
        jump_path=jump_path,
        dest_path=dest_path,
    )
    manager.deep_scan()
    
    # Verify queue size matches number of files
    assert manager.to_jump_queue.qsize() == len(file_sizes)
    
    # Verify total size matches expected
    assert manager.total_size == sum(file_sizes.values())
    
    # Verify all files are in queue
    queued_files = set()
    while not manager.to_jump_queue.empty():
        task: FileTransferTask = manager.to_jump_queue.get()
        queued_files.add(str(task.relative_path))
        assert task.size == file_sizes[str(task.relative_path)]
    
    assert queued_files == set(file_sizes.keys())

def test_full_transfer(test_file_structure: Tuple[Path, dict[str, int]], transfer_paths: Tuple[Path, Path, Path]):
    """Test the complete transfer process from source to jump to destination."""
    source_path, file_sizes = test_file_structure
    source_dir, jump_path, dest_path = transfer_paths
    
    # Copy test files to source directory
    for item in source_path.glob("**/*"):
        if item.is_file():
            relative_path = item.relative_to(source_path)
            dest_item = source_dir / relative_path
            dest_item.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_item)
    
    manager = TransferManager(
        source_path=source_dir,
        jump_path=jump_path,
        dest_path=dest_path,
    )
    
    # Run the transfer
    manager.deep_scan()
    manager.start_transfer()
    
    # Verify all files exist in jump drive
    for relative_path in file_sizes:
        jump_file = jump_path / relative_path
        assert jump_file.exists()
        assert jump_file.stat().st_size == file_sizes[relative_path]
    
    # Verify all files exist in destination
    for relative_path in file_sizes:
        dest_file = dest_path / relative_path
        assert dest_file.exists()
        assert dest_file.stat().st_size == file_sizes[relative_path]
    
    # Verify completion stats
    assert len(manager.completed_to_jump) == len(file_sizes)
    assert len(manager.completed_to_dest) == len(file_sizes)
    assert manager.transferred_to_jump == sum(file_sizes.values())
    assert manager.transferred_to_dest == sum(file_sizes.values())

def test_transfer_with_existing_files(test_file_structure: Tuple[Path, dict[str, int]], transfer_paths: Tuple[Path, Path, Path]):
    """Test transfer when some files already exist in the destination."""
    source_path, file_sizes = test_file_structure
    source_dir, jump_path, dest_path = transfer_paths
    
    # Copy test files to source directory
    for item in source_path.glob("**/*"):
        if item.is_file():
            relative_path = item.relative_to(source_path)
            dest_item = source_dir / relative_path
            dest_item.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_item)
    
    # Create some files in destination first
    first_file = next(iter(file_sizes))
    dest_file = dest_path / first_file
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    dest_file.write_text("Pre-existing content")
    
    manager = TransferManager(
        source_path=source_dir,
        jump_path=jump_path,
        dest_path=dest_path,
    )
    
    # Run the transfer
    manager.deep_scan()
    manager.start_transfer()
    
    # Verify the pre-existing file was overwritten
    assert dest_file.read_text() == (source_dir / first_file).read_text()
    
    # Verify all other files transferred correctly
    for relative_path in file_sizes:
        dest_file = dest_path / relative_path
        assert dest_file.exists()
        assert dest_file.stat().st_size == file_sizes[relative_path] 