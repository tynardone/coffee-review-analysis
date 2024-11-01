import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))


load_dotenv(os.path.join(basedir, ".env"))


class Config:
    BASEDIR = basedir
    OPENEXCHANGERATES_API_ID = os.environ.get("OPENEXCHANGERATES_API_ID")
    GEOCODE_API_KEY = os.environ.get("GEOCODE_API_KEY")
