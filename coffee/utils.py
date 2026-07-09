from datetime import datetime


def create_filename(filename: str, filetype: str) -> str:
    """Creates a filename of format 'YYYY-MM-DD_filename.filetype'"""
    current_date: str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{current_date}_{filename}.{filetype}"
    return filename
