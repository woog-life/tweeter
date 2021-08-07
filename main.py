import inspect
import logging
import os
import socket
import sys
from datetime import datetime, timezone
from typing import Tuple, Union, List

import pytz
import requests
import tweepy
import urllib3
# noinspection PyPackageRequirements
# it is there (python-telegram-bot)
from telegram import Bot

BACKEND_URL = os.getenv("BACKEND_URL") or "https://api.woog.life"
BACKEND_PATH = os.getenv("BACKEND_PATH") or "lake/{}"
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


def send_telegram_alert(message: str, token: str, chatlist: List[str]):
    logger = create_logger(inspect.currentframe().f_code.co_name)
    if not token:
        logger.error("TOKEN not defined in environment, skip sending telegram message")
        return

    if not chatlist:
        logger.error("chatlist is empty (env var: TELEGRAM_CHATLIST)")

    for user in chatlist:
        Bot(token=token).send_message(chat_id=user, text=f"Error while executing laketweet: {message}")


def get_temperature() -> Tuple[bool, Union[Tuple[float, str], str]]:
    logger = create_logger(inspect.currentframe().f_code.co_name)
    path = BACKEND_PATH.format(WOOG_UUID)
    url = "/".join([BACKEND_URL, path])

    logger.debug(f"Calling {url}")
    try:
        response = requests.get(url)
        logger.debug(f"success: {response.ok} | content: {response.content}")
    except (requests.exceptions.ConnectionError, socket.gaierror, urllib3.exceptions.MaxRetryError) as e:
        logger.exception(f"Error while connecting to backend ({url})", exc_info=True)
        return False, f"Error while connecting to backend: {e}"

    if response.ok:
        data = response.json().get("data")
        if not data:
            return False, "`data` is null"

        logger.debug(f"Extracting time/temperature from data ({data})")
        return True, (float(data.get("preciseTemperature")), data.get("time"))
    else:
        return False, f"Request to backend was unsuccessful: {response.content}"


def send_temperature_tweet(temperature: float, isotime: str) -> Tuple[bool, str]:
    logger = create_logger(inspect.currentframe().f_code.co_name)

    temperature = round(temperature, 2)
    # time: datetime = datetime.fromisoformat(isotime.replace("Z", ""))
    time = pytz.timezone("UTC").localize(datetime.fromisoformat(isotime.replace("Z", ""))).astimezone(
        pytz.timezone("Europe/Berlin"))

    time_formatted = time.strftime("%H:%M %d.%m.%Y")

    now = datetime.now(tz=timezone.utc).astimezone(pytz.timezone("Europe/Berlin"))
    diff = (now - time).total_seconds()
    logger.debug(f"time: {time} | now: {now} | diff: {diff}")
    if diff / 60 > 115:
        return False, "last timestamp is older than 115 minutes"

    auth = tweepy.OAuthHandler(CONSUMER_KEY, CONSUMER_SECRET)
    auth.set_access_token(ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

    api = tweepy.API(auth)
    if not api.verify_credentials():
        return False, "Couldn't verify credentials"

    message = f"Der Woog hat eine Temperatur von {temperature}°C ({time_formatted}) #woog #wooglife #darmstadt"
    logger.debug(f"updating status with: `{message}`")
    api.update_status(message)

    return True, ""


def main() -> Tuple[bool, str]:
    logger = create_logger(inspect.currentframe().f_code.co_name)
    success, value = get_temperature()

    if success:
        logger.debug("Success from api")
        temperature, time = value
        logger.debug(f"Successfully extracted time/temp from api ({time} {temperature})")
        return send_temperature_tweet(temperature, time)

    logger.error(f"Couldn't retrieve temp/time from api: {value}")
    return False, value


root_logger = create_logger("__main__")

if not WOOG_UUID:
    root_logger.error("LARGE_WOOG_UUID not defined in environment")
if not (CONSUMER_KEY and CONSUMER_SECRET and ACCESS_TOKEN and ACCESS_TOKEN_SECRET):
    root_logger.error("Some twitter key/secret is not defined in environment")
else:
    try:
        success, message = main()
    except Exception as e:
        success = False
        message = f"{e}"

    if not success:
        root_logger.error(f"Something went wrong ({message})")
        token = os.getenv("TOKEN")
        chatlist = os.getenv("TELEGRAM_CHATLIST") or ""
        send_telegram_alert(message, token=token, chatlist=chatlist.split(","))
        sys.exit(1)
