from datetime import datetime


def create_filename(filename: str, filetype: str) -> str:
    """Creates a filename of format 'ddmmyyyy_filename.filetype'"""
    current_date: str = datetime.now().strftime("%d%m%Y")
    filename = f"{current_date}_{filename}.{filetype}"
    return filename
