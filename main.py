import inspect
import logging
import os
import socket
import sys
import zoneinfo
from datetime import datetime, timezone
from typing import Tuple, Union

import requests
import urllib3
from mastodon import Mastodon

# noinspection PyPackageRequirements
# it is there (python-telegram-bot)

BACKEND_URL = os.getenv("BACKEND_URL") or "https://api.woog.life"
BACKEND_PATH = os.getenv("BACKEND_PATH") or "lake/{}/temperature"
WOOG_UUID = os.getenv("LARGE_WOOG_UUID")
CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
ACCESS_TOKEN_SECRET = os.getenv("ACCESS_TOKEN_SECRET")


def create_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    logger = logging.Logger(name)
    ch = logging.StreamHandler(sys.stdout)

    formatting = "[{}] %(asctime)s\t%(levelname)s\t%(module)s.%(funcName)s#%(lineno)d | %(message)s".format(name)
    formatter = logging.Formatter(formatting)
    ch.setFormatter(formatter)

    logger.addHandler(ch)
    logger.setLevel(level)

    return logger


def get_temperature(*, precision: int = 2, format_region: str = "DE") -> Tuple[bool, Union[Tuple[str, str], str]]:
    logger = create_logger(inspect.currentframe().f_code.co_name)
    path = BACKEND_PATH.format(WOOG_UUID)
    url = "/".join([BACKEND_URL, path])
    url += f"?precision={precision}&formatRegion={format_region}"

    logger.debug(f"Calling {url}")
    try:
        response = requests.get(url)
        logger.debug(f"success: {response.ok} | content: {response.content}")
    except (requests.exceptions.ConnectionError, socket.gaierror, urllib3.exceptions.MaxRetryError) as e:
        logger.exception(f"Error while connecting to backend ({url})", exc_info=True)
        return False, f"Error while connecting to backend: {e}"

    if response.ok:
        data = response.json()
        return True, (data.get("preciseTemperature"), data.get("time"))
    else:
        return False, f"Request to backend was unsuccessful: {response.content}"


def format_twoot(temperature: str, isotime: str) -> Tuple[bool, str]:
    logger = create_logger(inspect.currentframe().f_code.co_name)

    fromtime = datetime.fromisoformat(isotime.replace("Z", ""))
    utctime = datetime(fromtime.year, fromtime.month, fromtime.day, fromtime.hour, fromtime.minute, fromtime.second,
                       tzinfo=zoneinfo.ZoneInfo("UTC"))
    time = utctime.astimezone(zoneinfo.ZoneInfo("Europe/Berlin"))
    time_formatted = time.strftime("%H:%M %d.%m.%Y")

    now = datetime.now(tz=timezone.utc).astimezone(zoneinfo.ZoneInfo("Europe/Berlin"))
    diff_minutes = (now - time).total_seconds() / 60
    logger.debug(f"time: {time} | now: {now} | diff (min): {diff_minutes}")
    if diff_minutes > 115:
        return False, "last timestamp is older than 115 minutes"

    return True, f"Der Woog hat eine Temperatur von {temperature}°C ({time_formatted}) #woog #wooglife #darmstadt"


def send_temperature_toot(message: str) -> Tuple[bool, str]:
    access_token = os.getenv("MASTODON_ACCESS_TOKEN")
    instance_url = os.getenv("MASTODON_INSTANCE_URL", "https://mastodon.social")
    mastodon = Mastodon(api_base_url=instance_url, access_token=access_token)

    mastodon.toot(message)

    return True, ""


def main() -> Tuple[bool, str]:
    logger = create_logger(inspect.currentframe().f_code.co_name)
    # noinspection PyShadowingNames
    success, value = get_temperature()

    if success:
        logger.debug("Success from api")
        temperature, time = value
        logger.debug(f"Successfully extracted time/temp from api ({time} {temperature})")
        # noinspection PyShadowingNames
        success, message = format_twoot(temperature, time)
        if not success:
            return success, message

        return send_temperature_toot(message)

    logger.error(f"Couldn't retrieve temp/time from api: {value}")
    return False, value


if __name__ == "__main__":
    root_logger = create_logger("__main__")

    if not WOOG_UUID:
        root_logger.error("LARGE_WOOG_UUID not defined in environment")

    try:
        success, error_message = main()
    except Exception as e:
        success = False
        error_message = f"{e}"

    if not success:
        root_logger.error(f"Something went wrong ({error_message})")
        sys.exit(1)
