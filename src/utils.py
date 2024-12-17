from datetime import datetime


def create_filename(filename: str, filetype: str) -> str:
    """
    Creates a filename of format 'ddmmyyyy_filename.filetype

    Args:
        filename (str): Name of file after date prefix
        filetype (str): Type of file to save

    Returns:
        filename (str): Returns a filename of form
                        {current date ddmmyyyy}_{filename}.{filetype}
    '"""
    if filetype not in ("csv", "json"):
        raise ValueError("filetype must be one of: csv, json")
    current_date: str = datetime.now().strftime("%d%m%Y")
    filename = f"{current_date}_{filename}.{filetype}"
    return filename
