
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
- [Data Cleaning and Analysis](#data-cleaning-and-analysis)
- [License](#license)

## Project Overview

The project involves:

1. **Web Scraping**: Using Python to collect coffee reviews and associated metadata.
2. **Data Cleaning**: Processing the scraped data, handling missing values, and enriching it with additional information.
3. **Analysis**: Generating visualizations and insights into coffee characteristics, quality scores, and tasting notes.

The goal is to provide insights into the coffee market, with a particular focus on flavor trends, price indexes, and overall quality.

## Directory Structure

```plaintext
.
├── LICENSE
├── README.md
├── data
│   ├── external               # External datasets (e.g., CPI, exchange rates)
│   ├── intermediate           # Partially cleaned datasets
│   ├── processed              # Fully cleaned, ready-to-analyze datasets
│   └── raw                    # Raw, unprocessed scraped data
├── docs                       # Project documentation
├── imgs                       # Images for analysis results (e.g., word clouds)
├── main.py                    # Main entry point for the project
├── mypy.ini                   # Configuration for type checking
├── notebooks
│   ├── 1_data_cleaning.ipynb  # Notebook for initial data cleaning
│   ├── 2_data_EDA.ipynb       # Exploratory Data Analysis (EDA)
│   ├── 2_text_features.ipynb  # Text feature extraction and analysis
├── notes                      # Notes and research on related topics
├── requirements.txt           # Python package requirements
├── scripts                    # Utility scripts for data fetching and processing
├── src                        # Source code for the project
│   ├── async_parser.py        # Asynchronous parsers for reviews
│   ├── async_review_scraper.py # Asynchronous scraper for reviews
│   ├── async_url_scraper.py   # URL scraping logic
│   ├── config.py              # Project configuration settings
└── tests                      # Unit tests
```

## Installation

### Prerequisites

- Python 3.11+
- Virtual environment (e.g. `venv` or `pyenv`)
- Git (optional)

### Setup

1. **Clone this repository**:

    ```bash
    git clone https://github.com/tynardone/coffee-review-scraper.git
    cd coffee-review-scraper
    ```

    Or just download files from [repository](https://github.com/tynardone/coffee-review-analysis.git).

2. **Create and activate a virtual environment**:

    ```bash
    python -m venv venv
    source venv/bin/activate
    ```

3. **Install dependencies**:

    ```bash
    pip install -r requirements.txt
    ```

4. **Obtain free API Keys**:

    If you want to run data cleaning you will need two API keys, both available with free tiers.

    - [OpenExchangeRates](https://openexchangerates.org/signup/free)
    - [GeoCodingAPI](https://geocode.maps.co/)

    Add API keys to environment or .env file

    ```plaintext
    OPENEXCHANGERATES_API_KEY =
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

## Scripts

- async_scrape_roast_reviews.py
- async_scrape_roast_urls.py
- json_to_csv.py
- openex.py
- review_parse.py

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
