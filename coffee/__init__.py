"""Tools for scraping and analyzing CoffeeReview.com data.

This package implements the scraping half of the pipeline: discovering review
URLs (:mod:`async_url_scraper`), fetching them (:mod:`fetch`), and parsing each
page into structured records (:mod:`async_review_scraper`, :mod:`async_parser`),
plus shared configuration (:mod:`config`) and helpers (:mod:`utils`). Data
cleaning and analysis live in the project's notebooks.
"""
