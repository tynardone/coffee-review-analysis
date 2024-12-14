# type: ignore
import atexit
import json
import logging
import logging.config
from logging.handlers import QueueHandler

CONFIG_FILE = ""

logger = logging.getLogger("my_app")


def setup_logging() -> None:
    if not CONFIG_FILE:
        raise ValueError("Config file path is not set.")
    with open(CONFIG_FILE) as f_in:
        config = json.load(f_in)
    logging.config.dictConfig(config)
    queue_handler: QueueHandler | None = logging.getHandlerByName("queue_handler")
    if queue_handler:
        queue_handler.listener.start()
        atexit.register(queue_handler.listener.stop)
