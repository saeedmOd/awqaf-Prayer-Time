#!/usr/bin/env bash
# تثبيت سكربت أوقات الصلاة على سيرفر Linux.
#   MODE=cron ./install.sh        (افتراضي) نسخ + cron كل 5 دقائق
#   MODE=systemd ./install.sh     نسخ + مؤقّت systemd (يتطلب sudo)
#   MODE=none ./install.sh        نسخ الملفات فقط
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${TARGET:-/opt/awqaf-prayer}"
MODE="${MODE:-cron}"
RUN_USER="${RUN_USER:-$(id -un)}"

if [ "$(id -u)" -eq 0 ] || [ -w "$(dirname "$TARGET")" ]; then
  install -d "$TARGET"
else
  TARGET="${HOME}/awqaf-prayer"
  install -d "$TARGET"
  echo "لا صلاحية على /opt، تم استخدام: $TARGET"
fi

install -m 0755 "$SCRIPT_DIR/prayer_alain.py" "$TARGET/prayer_alain.py"
if [ ! -f "$TARGET/config.env" ]; then
  install -m 0600 "$SCRIPT_DIR/config.example.env" "$TARGET/config.env"
  echo "أنشئ $TARGET/config.env — عدّل MQTT_HOST قبل التشغيل."
fi

python3 "$TARGET/prayer_alain.py" --dry-run || {
  echo "فشل الاختبار. عدّل $TARGET/config.env ثم أعد المحاولة." >&2
  exit 1
}

case "$MODE" in
  cron)
    ( crontab -l 2>/dev/null | grep -v 'prayer_alain.py' || true
      echo "*/5 * * * * cd $TARGET && /usr/bin/python3 prayer_alain.py >> $TARGET/prayer.log 2>&1"
    ) | crontab -
    echo "تمت الجدولة عبر cron كل 5 دقائق."
    ;;
  systemd)
    sudo sed -e "s|__USER__|$RUN_USER|g" -e "s|__TARGET__|$TARGET|g" \
      "$SCRIPT_DIR/systemd/awqaf-prayer.service" > /tmp/awqaf-prayer.service
    sudo cp /tmp/awqaf-prayer.service /etc/systemd/system/awqaf-prayer.service
    sudo cp "$SCRIPT_DIR/systemd/awqaf-prayer.timer" /etc/systemd/system/awqaf-prayer.timer
    sudo systemctl daemon-reload
    sudo systemctl enable --now awqaf-prayer.timer
    echo "تم تفعيل awqaf-prayer.timer."
    ;;
  none)
    echo "تم النسخ إلى $TARGET دون جدولة."
    ;;
  *)
    echo "MODE غير معروف: $MODE (cron|systemd|none)" >&2
    exit 1
    ;;
esac

echo "لتشغيل Discovery مرة واحدة: python3 $TARGET/prayer_alain.py --discovery"
