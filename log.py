import atexit
import json
import logging

CONFIG_FILE = ""

logger = logging.getLogger("my_app")


def setup_logging():
    if not CONFIG_FILE:
        raise ValueError("Config file path is not set.")
    with open(CONFIG_FILE) as f_in:
        config = json.load(f_in)
    logging.config.dictConfig(config)
    queue_handler: logging.handlers.QueueHandler = logging.getHandlerByName(
        "queue_handler"
    )
    if queue_handler is not None:
        queue_handler.listener.start()
        atexit.register(queue_handler.listener.stop)
