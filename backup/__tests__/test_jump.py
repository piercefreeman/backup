import pytest
from pathlib import Path
import os
import shutil
import tempfile
import time
from typing import Generator, Tuple
import threading

from backup.jump import TransferManager, FileTransferTask

@pytest.fixture
def test_file_structure() -> Generator[Tuple[Path, dict[str, bytes]], None, None]:
    """Create a temporary directory with test files and return the path and file contents.
    
    Returns:
        Tuple of (temp_dir, file_contents) where file_contents is a dict of relative paths to content
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        root_path = Path(temp_dir)
        
        # Create a nested structure of test files with known content
        file_contents = {}
        
        # Create some files in root
        for i in range(3):
            file_path = root_path / f"file_{i}.txt"
            content = f"Content for file {i}\n" * (i + 1)
            content_bytes = content.encode()
            file_path.write_bytes(content_bytes)
            file_contents[f"file_{i}.txt"] = content_bytes
        
        # Create nested directory structure
        nested_dir = root_path / "nested" / "subdirectory"
        nested_dir.mkdir(parents=True)
        
        for i in range(2):
            file_path = nested_dir / f"nested_file_{i}.txt"
            content = f"Nested content for file {i}\n" * (i + 2)
            content_bytes = content.encode()
            file_path.write_bytes(content_bytes)
            file_contents[str(Path("nested/subdirectory") / f"nested_file_{i}.txt")] = content_bytes
            
        # Create a file with special characters
        special_file = root_path / "special_#$@!.txt"
        special_content = "Special content\n"
        special_content_bytes = special_content.encode()
        special_file.write_bytes(special_content_bytes)
        file_contents["special_#$@!.txt"] = special_content_bytes
        
        yield root_path, file_contents

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

def setup_source_files(source_dir: Path, test_structure: Tuple[Path, dict[str, bytes]]) -> None:
    """Helper to set up source directory with test files."""
    source_path, file_contents = test_structure
    for relative_path, content in file_contents.items():
        dest_file = source_dir / relative_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_bytes(content)

def verify_files_match(dir_path: Path, file_contents: dict[str, bytes]) -> None:
    """Helper to verify files in directory match expected contents."""
    for relative_path, expected_content in file_contents.items():
        file_path = dir_path / relative_path
        assert file_path.exists(), f"File {relative_path} should exist"
        assert file_path.read_bytes() == expected_content, f"Content mismatch for {relative_path}"

def test_scanner_generator(test_file_structure: Tuple[Path, dict[str, bytes]], transfer_paths: Tuple[Path, Path, Path]):
    """Test that the file scanner generator works correctly."""
    source_dir, jump_path, dest_path = transfer_paths
    setup_source_files(source_dir, test_file_structure)
    
    manager = TransferManager(
        source_path=source_dir,
        jump_path=jump_path,
        dest_path=dest_path,
    )
    
    # Collect all tasks from generator
    tasks = list(manager.scan_files())
    
    # Verify task count
    assert len(tasks) == len(test_file_structure[1])
    
    # Verify all files are found
    found_paths = {str(task.relative_path) for task in tasks}
    expected_paths = set(test_file_structure[1].keys())
    assert found_paths == expected_paths

def test_full_transfer_flow(test_file_structure: Tuple[Path, dict[str, bytes]], transfer_paths: Tuple[Path, Path, Path]):
    """Test the complete transfer process from source to jump to destination."""
    source_dir, jump_path, dest_path = transfer_paths
    setup_source_files(source_dir, test_file_structure)
    
    manager = TransferManager(
        source_path=source_dir,
        jump_path=jump_path,
        dest_path=dest_path,
    )
    
    # Run the transfer
    manager.start_transfer()
    
    # Verify files in jump drive
    verify_files_match(jump_path, test_file_structure[1])
    
    # Verify files in destination
    verify_files_match(dest_path, test_file_structure[1])
    
    # Verify completion stats
    assert len(manager.completed_to_jump) == len(test_file_structure[1])
    assert len(manager.completed_to_dest) == len(test_file_structure[1])

def test_skip_existing_files(test_file_structure: Tuple[Path, dict[str, bytes]], transfer_paths: Tuple[Path, Path, Path]):
    """Test that files are properly skipped when they exist at destination."""
    source_dir, jump_path, dest_path = transfer_paths
    setup_source_files(source_dir, test_file_structure)
    
    # Create some files in destination first
    file_to_skip = next(iter(test_file_structure[1].items()))
    skip_path = dest_path / file_to_skip[0]
    skip_path.parent.mkdir(parents=True, exist_ok=True)
    skip_path.write_bytes(file_to_skip[1])
    
    manager = TransferManager(
        source_path=source_dir,
        jump_path=jump_path,
        dest_path=dest_path,
    )
    
    # Run the transfer
    manager.start_transfer()
    
    # Verify skipped file wasn't overwritten
    assert skip_path.read_bytes() == file_to_skip[1]
    assert file_to_skip[0] in {str(p) for p in manager.skipped_files}
    
    # Verify other files transferred
    for rel_path, content in test_file_structure[1].items():
        if rel_path != file_to_skip[0]:
            dest_file = dest_path / rel_path
            assert dest_file.exists()
            assert dest_file.read_bytes() == content
