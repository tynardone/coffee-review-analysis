from datetime import datetime
from pathlib import Path


def create_filename(filename: str, filetype: str) -> str:
    """Creates a filename of format 'ddmmyyyy_filename.filetype'"""
    current_date: str = datetime.now().strftime("%d%m%Y")
    filename = f"{current_date}_{filename}.{filetype}"
    return filename


def create_filepath(filename: str, filetype: str) -> Path:
    """Creates a filepath for the file in the 'data/raw' directory."""
    if filetype not in ("csv", "json", "pkl"):
        raise ValueError(
            "Invalid file type. Only 'csv', 'json', and 'pkl' are supported."
        )
    data_dir = Path("data/raw/")
    return data_dir / create_filename(filename, filetype)
