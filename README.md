
# Coffee Review Scraper and Analysis

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-Ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

This project is a complete data pipeline for scraping coffee reviews from [CoffeeReview.com](https://www.coffeereview.com/), followed by data cleaning, transformation, and analysis. The data is augmented with additional external datasets (e.g., consumer price index, exchange rates) and analyzed to explore trends in coffee quality.

## Table of Contents

- [Project Overview](#project-overview)
- [Directory Structure](#directory-structure)
- [Installation](#installation)
- [Data Sources](#data-sources)
- [Code Layout](#code-layout)
- [Usage](#usage)

## Project Overview

The project involves:

1. **Web Scraping**: Using Python to scrape coffee reviews and associated metadata and save to CSV.
2. **Data Cleaning**: Processing the scraped data, handling missing values, extensive cleaning and normalization, and augmenting with additional information.
3. **Analysis**: Generating visualizations and insights into coffee characteristics, quality scores, and tasting notes.

The goal is to provide insights into the boutique coffee market, with a focus on origin, price, flavor notes and quality metrics.

## Directory Structure

```plaintext
.
├── LICENSE
├── README.md
├── coffee
│   ├── __init__.py
│   ├── config.py
│   ├── fetch.py
│   ├── parser.py
│   ├── review_scraper.py
│   ├── review_urls.py
│   ├── test_html
│   └── utils.py
├── data
│   ├── external
│   ├── intermediate
│   ├── processed
│   └── raw
├── imgs
├── notebooks
│   ├── 01-data-cleaning.ipynb
│   ├── 02-data-EDA.ipynb
│   ├── 03-text-features.ipynb
│   └── wordcloud.png
├── notes
├── pyproject.toml
├── scripts
│   ├── archive
│   ├── openex.py
│   ├── resolve_roasters.py
│   └── scrape_reviews.py
└── uv.lock
```

## Installation

### Prerequisites

- Python 3.12 (the project targets `>=3.12,<3.14`)
- [uv](https://docs.astral.sh/uv/) for dependency and environment management
- Git (optional)

### Setup

1. **Clone this repository**:

    ```bash
    git clone https://github.com/tynardone/coffee-review-analysis.git
    cd coffee-review-analysis
    ```

2. **Install dependencies**:

    `uv sync` creates a virtual environment in `.venv`, installs the pinned
    dependencies from `uv.lock`, and installs the `coffee` package itself in
    editable mode:

    ```bash
    uv sync
    ```

    This installs the `analysis` and `dev` dependency groups by default. For a
    lean environment with just the scraping/pipeline dependencies, run
    `uv sync --no-default-groups`.

3. **Obtain free API Keys**:

    If you want to run data cleaning you will need two API keys, both available with free tiers.

    - [OpenExchangeRates](https://openexchangerates.org/signup/free)
    - [GeoCodingAPI](https://geocode.maps.co/)

    Add API keys to environment or .env file

    ```plaintext
    OPENEXCHANGERATES_API_ID =
    GEOCODE_API_KEY =
    ```

## Data Sources

- **CoffeeReview.com**

    Source of the dataset of coffee roast reviews and target of webscraper.
    Operating since 1997 and amassing 1000s of blind-taste reviews of coffee roasts from around the world.
    The raw scraped data requires significant cleanup.

- **OpenExchangeRates**

    Provider of historical and up-to-date currency exchange rates. Used to convert price data to a single currency. They offer free API access limited to 1000 requests per month.

- **Geocoding API**

    A free geocoding API from [Map Maker](https://maps.co/). Geocoding is the process of converting addresses into latitude and longitude coordinates. This is done to provide coordinates of roasters and origin locations for potential future spatial analysis or visualization.

## Code Layout

Reusable logic lives in the `coffee/` package; runnable pipeline steps live in
`scripts/`.

**`coffee/` (importable package)**

- `review_urls.py` — crawls the paginated review listings (breadth-first) to
  discover individual review URLs.
- `review_scraper.py` — fetches a review page and parses it into a record.
- `parser.py` — parses review HTML into structured fields.
- `fetch.py` — shared async HTTP GET with bounded concurrency and retry, used by
  both discovery and scraping.
- `config.py` — configuration, paths, and API keys (loaded from the environment
  / `.env`).
- `utils.py` — small helpers (e.g. dated filename generation).

**`scripts/` (runnable steps)**

- `scrape_reviews.py` — end-to-end scrape: discovers review URLs, scrapes every
  review, and writes a dated CSV + JSON to `data/raw/`.
- `openex.py` — fetches historical exchange rates for the scraped review dates.
- `resolve_roasters.py` — normalizes roaster names.
- `archive/` — one-off / retired scripts kept for reference.

## Usage

Run from the repository root. `uv run` executes commands inside the project's
virtual environment without needing to activate it:

```bash
# Scrape all reviews into data/raw/<YYYY-MM-DD>_reviews.{csv,json}
uv run python scripts/scrape_reviews.py

# Fetch historical exchange rates for the scraped review dates
uv run python scripts/openex.py

# Launch Jupyter for the analysis notebooks
uv run jupyter lab
```

## Historical Exchange Rates

<https://docs.openexchangerates.org/reference/api-introduction>

## US Consumer Price Index

Consumer Price Index for All Urban Consumers (CPI-U)
Not Seasonally Adjusted CPI for All Urban Consumers (CPI-U): U.S. city average
All items in U.S. city average, all urban consumers, not seasonally adjusted

consumer_price_index.csv

<https://www.bls.gov/cpi/data.htm>

1. Scrape and parse
2. Data cleaning
3. Cleaning and reconciliation in OpenRefine
4. Feature engineering from text

# t-SNE

<https://distill.pub/2016/misread-tsne/>
