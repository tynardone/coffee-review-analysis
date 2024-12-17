import atexit
import json
import logging
import logging.config
from logging.handlers import QueueHandler

CONFIG_FILE: str = "loggingconfig.json"

logger = logging.getLogger("my_app")


def setup_logging() -> None:
    """
    Load dictionary configuration for logging and initialize queue handler listener.
    When loaded from dictConfig QueueHandler has a QueueListener under
    `listener` attribute. If the "queue" key is absent in config, a standard unbounded
    queue.Queue instance is created and used.
    """
    if not CONFIG_FILE:
        raise ValueError("Config file path is not set.")

    with open(CONFIG_FILE) as f_in:
        config = json.load(f_in)

    logging.config.dictConfig(config)
    handler_name: str = "queue_handler"
    handler = logging.getHandlerByName(handler_name)
    if isinstance(handler, QueueHandler):
        # `listener` not recognized by mypy but is valid at runtime
        handler.listener.start()  # type: ignore[attr-defined]
        atexit.register(handler.listener.stop)  # type: ignore[attr-defined]
    else:
        raise TypeError(f"Handler {handler_name} not loaded from config properly")


def main():
    setup_logging()

    # Test logging at different levels
    logger.debug("This is a DEBUG message")
    logger.info("This is an INFO message")
    logger.warning("This is a WARNING message")
    logger.error("This is an ERROR message")
    logger.critical("This is a CRITICAL message")

    # Output some message indicating logging works
    print("Logging messages have been sent.")


if __name__ == "__main__":
    main()
