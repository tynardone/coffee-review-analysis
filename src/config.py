import os

from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))


load_dotenv(os.path.join(basedir, ".env"))


class Config:
    BASEDIR = basedir
    OPENEXCHANGERATES_API_ID = os.environ.get("OPENEXCHANGERATES_API_ID")
    GEOCODE_API_KEY = os.environ.get("GEOCODE_API_KEY")
    BASE_URL = "https://www.coffeereview.com/review/"
    HEADERS = {
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
        )
    }


class OpenExConfig(Config):
    API_URL: str = "https://openexchangerates.org/api/historical/"
    TIMEOUT: int = 10
