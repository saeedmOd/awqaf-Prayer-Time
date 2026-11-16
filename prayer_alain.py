#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""أوقات الصلاة الرسمية في الإمارات -> MQTT -> Home Assistant.

يقرأ المواقيت الرسمية من واجهة الهيئة العامة للشؤون الإسلامية والأوقاف
(internationalprayertime.awqaf.gov.ae) وينشرها على مواضيع MQTT محفوظة
(retained) تستهلكها Home Assistant، مع دعم MQTT Discovery لإنشاء الكيانات
تلقائياً على أي تثبيت جديد.

أمثلة:
    python3 prayer_alain.py                  # نشر المواقيت الآن
    python3 prayer_alain.py --dry-run        # عرض القيم دون نشر
    python3 prayer_alain.py --discovery      # نشر إعدادات Discovery مرة واحدة
    python3 prayer_alain.py --list-locations # عرض المدن ومعرّفاتها الرسمية
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

API_URL = "https://internationalprayertime.awqaf.gov.ae/api/v1/prayertimes"
LOCATIONS_URL = "https://internationalprayertime.awqaf.gov.ae/api/v1/location/emirates?lang=en"

# مفتاح الواجهة العامة المنشور داخل كود موقع الأوقاف نفسه.
# يمكن استبداله بمتغيّر البيئة AWQAF_API_KEY.
DEFAULT_API_KEY = "ak_1148a6086913233acc33709fc9aa6e93"

PRAYER_KEYS = ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")
API_FIELD = {
    "fajr": "fajer",
    "sunrise": "shuroq",
    "dhuhr": "dhohur",
    "asr": "aser",
    "maghrib": "maghreb",
    "isha": "isha",
}

AR_NAME = {
    "fajr": "الفجر",
    "sunrise": "الشروق",
    "dhuhr": "الظهر",
    "asr": "العصر",
    "maghrib": "المغرب",
    "isha": "العشاء",
}

SENSOR_KEYS = PRAYER_KEYS + ("next_prayer", "date_hijri", "date_gregorian")
SENSOR_NAME = {
    "fajr": "صلاة الفجر",
    "sunrise": "الشروق",
    "dhuhr": "صلاة الظهر",
    "asr": "صلاة العصر",
    "maghrib": "صلاة المغرب",
    "isha": "صلاة العشاء",
    "next_prayer": "الصلاة القادمة",
    "date_hijri": "التاريخ الهجري",
    "date_gregorian": "التاريخ الميلادي",
}
SENSOR_ICON = {
    "next_prayer": "mdi:clock-fast",
    "date_hijri": "mdi:calendar-islamic",
    "date_gregorian": "mdi:calendar-clock",
}
DEFAULT_ICON = "mdi:clock-outline"

HIJRI_MONTHS = {
    1: "مُحَرَّم",
    2: "صَفَر",
    3: "رَبيع الأوَّل",
    4: "رَبيع الآخِر",
    5: "جُمَادَى الأُولَى",
    6: "جُمَادَى الآخِرَة",
    7: "رَجَب",
    8: "شَعْبَان",
    9: "رَمَضَان",
    10: "شَوَّال",
    11: "ذُو القِعْدَة",
    12: "ذُو الحِجَّة",
}

DEFAULT_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.env")


# --------------------------------------------------------------------------
# الإعدادات
# --------------------------------------------------------------------------
def load_env_file(path):
    """يقرأ متغيّرات البيئة من ملف config.env إن وُجد (لا يستبدل ما هو مضبوط)."""
    if not path or not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def as_bool(value, default=True):
    if value is None or value == "":
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off")


class Config:
    def __init__(self, args):
        env = os.environ
        self.api_key = args.api_key or env.get("AWQAF_API_KEY") or DEFAULT_API_KEY
        self.official_id = int(args.official_id or env.get("AWQAF_OFFICIAL_ID") or 37)
        self.city = args.city or env.get("AWQAF_CITY") or "مدينة العين"
        self.timezone = env.get("AWQAF_TZ") or "Asia/Dubai"
        self.mqtt_host = args.mqtt_host or env.get("MQTT_HOST") or "localhost"
        self.mqtt_port = int(args.mqtt_port or env.get("MQTT_PORT") or 1883)
        self.mqtt_username = env.get("MQTT_USERNAME") or None
        self.mqtt_password = env.get("MQTT_PASSWORD") or None
        self.topic_prefix = (env.get("MQTT_TOPIC_PREFIX")
                             or "homeassistant/sensor/prayer_alain").rstrip("/")
        self.discovery_prefix = (env.get("MQTT_DISCOVERY_PREFIX")
                                 or "homeassistant").rstrip("/")
        self.retain = as_bool(env.get("MQTT_RETAIN"), True)
        self.timeout = int(env.get("MQTT_TIMEOUT") or 15)
        self.client_id = (env.get("MQTT_CLIENT_ID")
                          or "awqaf-prayer-" + socket.gethostname()[:23])
        self.dry_run = args.dry_run
        self.verbose = args.verbose
        self.tz = ZoneInfo(self.timezone)

    def state_topic(self, key):
        return "%s_%s/state" % (self.topic_prefix, key)

    def discovery_topic(self, key):
        return "%s/sensor/%s_%s/config" % (self.discovery_prefix,
                                           self.topic_prefix.rsplit("/", 1)[-1], key)

    @property
    def device_name(self):
        return "أوقات الصلاة الرسمية في %s" % self.city


# --------------------------------------------------------------------------
# MQTT (تنفيذ بسيط بلا مكتبات خارجية)
# --------------------------------------------------------------------------
def _mqtt_string(text):
    data = text.encode("utf-8")
    return len(data).to_bytes(2, "big") + data


def _remaining_length(size):
    out = bytearray()
    while True:
        byte = size % 128
        size //= 128
        out.append(byte | 0x80 if size > 0 else byte)
        if size == 0:
            return bytes(out)


class MqttClient:
    """اتصال واحد، QoS0، نشرة اختيارية محفوظة (retained)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.sock = None

    def __enter__(self):
        cfg = self.cfg
        sock = socket.create_connection((cfg.mqtt_host, cfg.mqtt_port), cfg.timeout)
        sock.settimeout(cfg.timeout)
        flags = 0x02  # clean session
        if cfg.mqtt_username:
            flags |= 0x80
        if cfg.mqtt_password:
            flags |= 0x40
        body = _mqtt_string("MQTT") + bytes([4, flags]) + (60).to_bytes(2, "big")
        body += _mqtt_string(cfg.client_id)
        if cfg.mqtt_username:
            body += _mqtt_string(cfg.mqtt_username)
        if cfg.mqtt_password:
            body += _mqtt_string(cfg.mqtt_password)
        sock.sendall(b"\x10" + _remaining_length(len(body)) + body)
        ack = sock.recv(4)
        if len(ack) < 4 or ack[0] != 0x20 or ack[3] != 0:
            sock.close()
            raise RuntimeError("فشل الاتصال بـ MQTT (CONNACK: %r)" % (ack,))
        self.sock = sock
        return self

    def publish(self, topic, payload, retain=None):
        retain = self.cfg.retain if retain is None else retain
        data = _mqtt_string(topic) + payload.encode("utf-8")
        header = 0x31 if retain else 0x30
        self.sock.sendall(bytes([header]) + _remaining_length(len(data)) + data)
        if self.cfg.verbose:
            print("  -> %s = %s" % (topic, payload[:80]))

    def __exit__(self, *exc):
        if self.sock is not None:
            try:
                self.sock.sendall(b"\xe0\x00")  # DISCONNECT
            finally:
                self.sock.close()
                self.sock = None
        return False


# --------------------------------------------------------------------------
# واجهة الأوقاف
# --------------------------------------------------------------------------
def api_request(cfg, url, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "x-api-key": cfg.api_key,
        "origin": "https://www.awqaf.gov.ae",
        "user-agent": "awqaf-prayer-mqtt/2.0",
    }
    if data is not None:
        headers["content-type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8", "replace"))
    if body.get("errors"):
        raise RuntimeError("أخطاء من الواجهة: %s" % body["errors"])
    return body


def fetch_times(cfg, start, end):
    body = api_request(cfg, API_URL, {
        "start_date": start,
        "end_date": end,
        "official_id": cfg.official_id,
    })
    rows = body["result"]["prayer_times"]
    if not rows:
        raise RuntimeError("لا توجد بيانات لهذا التاريخ/المعرّف")
    return rows[0], (rows[1] if len(rows) > 1 else rows[0])


def list_locations(cfg):
    body = api_request(cfg, LOCATIONS_URL)
    for emirate in body["result"]:
        print("== %s" % emirate["name"])
        for city in emirate.get("cities", []):
            print("   %-28s official_id=%-4s area_id=%s"
                  % (city["name"], city["official_id"], city["id"]))


# --------------------------------------------------------------------------
# القيم المنشورة
# --------------------------------------------------------------------------
def hijri_text(hijri):
    month = HIJRI_MONTHS.get(hijri["month"], hijri.get("month_name", ""))
    return "%d %s %d هـ" % (hijri["day"], month, hijri["year"])


def next_prayer_state(cfg, now, today, tomorrow):
    for key in PRAYER_KEYS:
        hour, minute = (int(part) for part in today[API_FIELD[key]].split(":"))
        if hour * 60 + minute > now.hour * 60 + now.minute:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return AR_NAME[key], today[API_FIELD[key]], "", target

    for key in PRAYER_KEYS:
        hour, minute = (int(part) for part in tomorrow[API_FIELD[key]].split(":"))
        target = (now + timedelta(days=1)).replace(hour=hour, minute=minute,
                                                   second=0, microsecond=0)
        return AR_NAME[key], tomorrow[API_FIELD[key]], " (غداً)", target
    return None, None, "", now


def build_states(cfg, now, today, tomorrow):
    states = {key: today[API_FIELD[key]] for key in PRAYER_KEYS}

    name, clock, suffix, target = next_prayer_state(cfg, now, today, tomorrow)
    left = max(0, int((target - now).total_seconds() // 60))
    states["next_prayer"] = "%s%s في %s (متبقي %dس و %dد)" % (
        name, suffix, clock, left // 60, left % 60)

    states["date_hijri"] = hijri_text(today["hijri_date"])
    year, month, day = today["date"].split("-")
    states["date_gregorian"] = "%s-%s-%s" % (day, month, year)
    return states


def discovery_payload(cfg, key):
    return {
        "name": SENSOR_NAME[key],
        "state_topic": cfg.state_topic(key),
        "icon": SENSOR_ICON.get(key, DEFAULT_ICON),
        "unique_id": "uae_prayer_%s" % key,
        "device": {
            "identifiers": ["uae_prayer_scraper"],
            "name": cfg.device_name,
            "manufacturer": "الهيئة العامة للشؤون الإسلامية والأوقاف",
        },
    }


# --------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="نشر أوقات الصلاة الرسمية (هيئة الأوقاف الإماراتية) على MQTT.")
    parser.add_argument("--official-id", type=int,
                        help="معرّف المدينة الرسمي (العين=37). اعرض القائمة بـ--list-locations")
    parser.add_argument("--city", help="اسم المدينة كما يظهر في اسم الجهاز")
    parser.add_argument("--api-key", help="مفتاح واجهة الأوقاف (أو AWQAF_API_KEY)")
    parser.add_argument("--mqtt-host", help="عنوان وسيط MQTT (أو MQTT_HOST)")
    parser.add_argument("--mqtt-port", type=int, help="منفذ MQTT (أو MQTT_PORT)")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE,
                        help="ملف الإعدادات (الافتراضي config.env بجانب السكربت)")
    parser.add_argument("--discovery", action="store_true",
                        help="نشر إعدادات MQTT Discovery لإنشاء الكيانات")
    parser.add_argument("--list-locations", action="store_true",
                        help="عرض المحافظات والمدن ومعرّفاتها الرسمية")
    parser.add_argument("--dry-run", action="store_true", help="عرض القيم دون نشر")
    parser.add_argument("--verbose", action="store_true", help="طباعة كل رسالة MQTT")
    args = parser.parse_args(argv)

    load_env_file(args.env_file)
    cfg = Config(args)

    if args.list_locations:
        list_locations(cfg)
        return 0

    now = datetime.now(cfg.tz)
    today, tomorrow = fetch_times(cfg,
                                  now.strftime("%Y-%m-%d"),
                                  (now + timedelta(days=1)).strftime("%Y-%m-%d"))
    states = build_states(cfg, now, today, tomorrow)

    if args.dry_run:
        print("المدينة: %s (official_id=%s)" % (cfg.city, cfg.official_id))
        for key in SENSOR_KEYS:
            print("  %-16s %s" % (key, states[key]))
        if args.discovery:
            print(json.dumps(discovery_payload(cfg, "fajr"), ensure_ascii=False, indent=1))
        return 0

    with MqttClient(cfg) as client:
        if args.discovery:
            for key in SENSOR_KEYS:
                client.publish(cfg.discovery_topic(key),
                               json.dumps(discovery_payload(cfg, key), ensure_ascii=False),
                               retain=True)
        for key in SENSOR_KEYS:
            client.publish(cfg.state_topic(key), states[key])

    print("%s | %s" % (now.strftime("%F %T"), states["next_prayer"]))
    print("times: " + ", ".join("%s=%s" % (AR_NAME[k], states[k]) for k in PRAYER_KEYS))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except (urllib.error.URLError, RuntimeError, OSError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(1)
