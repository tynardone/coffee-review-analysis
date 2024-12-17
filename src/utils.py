from datetime import datetime


def create_filename(filename: str, filetype: str) -> str:
    """
     Generates a filename prefixed with the current date.

    This function takes a base filename and file type, validates the file type, and
    returns a formatted filename with the current date (in `ddmmyyyy` format) as
    a prefix.

    Args:
        filename (str): Name of file after date prefix
        filetype (str): Type of file to save

    Returns:
        filename (str): Returns a filename of form
                        {current date ddmmyyyy}_{filename}.{filetype}

    Raises:
        ValueError: If the provided fieltype is not "csv" or "json".
    '"""
    if filetype not in ("csv", "json"):
        raise ValueError("filetype must be one of: csv, json")
    current_date: str = datetime.now().strftime("%d%m%Y")
    filename = f"{current_date}_{filename}.{filetype}"
    return filename
