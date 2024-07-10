"""This script is used to convert the JSON file from review scraper to a CSV file.
Need to provide the path to the JSON file as the first argument."""

import argparse
import logging
from pathlib import Path
import pandas as pd

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s')


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Convert JSON to CSV")
    parser.add_argument("filename", type=str, help="Path to the JSON file")
    parser.add_argument("-o", "--output", type=str, help="Output directory")
    return parser.parse_args()


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """Clean column names."""
    df.columns = (df.columns.str.lower()
                  .str.replace(' ', '_', regex=False)
                  .str.replace(':', '', regex=False)
                  .str.replace('.', '', regex=False))
    return df


def create_csv_filepath(json_filepath: Path, output_dir: Path) -> Path:
    """Create the output CSV filepath."""
    csv_filepath = output_dir / json_filepath.with_suffix('.csv').name
    output_dir.mkdir(parents=True, exist_ok=True)
    return csv_filepath


def validate_input_file(filepath: Path) -> bool:
    """Check if the file exists and is a JSON file."""
    if not filepath.exists() or filepath.suffix.lower() != '.json':
        logging.error("Input file must be a valid JSON file.")
        return False
    return True


def main():
    """Runs script"""
    args = parse_args()

    json_filepath = Path(args.filename)
    output_dir = Path(args.output) if args.output else json_filepath.parent

    if not validate_input_file(json_filepath):
        return

    try:
        df = pd.read_json(json_filepath)
        df = clean_data(df)
        csv_filepath = create_csv_filepath(json_filepath, output_dir)
        df.to_csv(csv_filepath, index=False)
        logging.info("Conversion successful. CSV saved at: %s", csv_filepath)
    except pd.errors.EmptyDataError:
        logging.error("The provided JSON file is empty.")
    except ValueError as ve:
        logging.error("Value Error: %s", ve)


if __name__ == "__main__":
    main()
