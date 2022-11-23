import inspect
import json
import logging
import os
import socket
import sys
import zoneinfo
from datetime import datetime, timezone
from http.client import HTTPSConnection
from typing import Tuple, Union, List, Dict, Any, Optional

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

ROUTING_KEY = os.getenv("PAGERDUTY_ROUTING_KEY")


def build_pagerduty_alert(title: str, alert_body: str, dedup: str) -> Dict[str, Any]:
    return {
        "routing_key": ROUTING_KEY,
        "event_action": "trigger",
        "dedup_key": dedup,
        "payload": {
            "summary": title,
            "source": "tweeter",
            "severity": "critical",
            "custom_details": {
                "alert_body": alert_body,
            },
        },
    }


def send_pagerduty_alert(title: str, alert_body: str, dedup: Optional[str] = None) -> None:
    if dedup is None:
        dedup = str(datetime.utcnow().timestamp())
    url = "events.pagerduty.com"
    route = "/v2/enqueue"

    conn = HTTPSConnection(host=url, port=443)
    conn.request("POST", route, json.dumps(build_pagerduty_alert(title, alert_body, dedup)))
    result = conn.getresponse()
    print(result.read())


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
    url += "?precision=2&formatRegion=DE"

    logger.debug(f"Calling {url}")
    try:
        response = requests.get(url)
        logger.debug(f"success: {response.ok} | content: {response.content}")
    except (requests.exceptions.ConnectionError, socket.gaierror, urllib3.exceptions.MaxRetryError):
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

    auth = tweepy.OAuth1UserHandler(CONSUMER_KEY, CONSUMER_SECRET)
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
        token = os.getenv("BOT_ERROR_TOKEN")
        chatlist = os.getenv("TELEGRAM_CHATLIST") or ""
        send_telegram_alert(message, token=token, chatlist=chatlist.split(","))
        send_pagerduty_alert("tweeter failure", message)
        sys.exit(1)
