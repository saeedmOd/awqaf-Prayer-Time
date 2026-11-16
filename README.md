# أوقات الصلاة الرسمية (الإمارات) → MQTT → Home Assistant

سكربت واحد بلا مكتبات خارجية يجلب **المواقيت الرسمية** من واجهة الهيئة العامة للشؤون الإسلامية والأوقاف
(`internationalprayertime.awqaf.gov.ae`) وينشرها على وسيط MQTT، فتظهر في Home Assistant كجهاز واحد
يحمل اسم **«أوقات الصلاة الرسمية في مدينة العين»**.

- مصدر البيانات: الهيئة العامة للشؤون الإسلامية والأوقاف — المواقيت الرسمية (لا حساب فلكي محلي).
- يعمل على أي سيرفر Linux فيه Python 3.9+ (لا `pip install` إطلاقاً).
- المواضيع **محفوظة (retained)**، فتبقى القيم موجودة بعد إعادة تشغيل Home Assistant أو السيرفر.
- يدعم MQTT Discovery لإنشاء الكيانات تلقائياً على أي تثبيت جديد.

---

## 1) التثبيت السريع

```bash
git clone https://github.com/saeedmOd/awqaf-Prayer-Time.git
cd awqaf-prayer-times-ha

cp config.example.env config.env
nano config.env                     # عنوان وسيط MQTT ومعرّف المدينة

python3 prayer_alain.py --dry-run   # تحقّق من القيم أولاً
python3 prayer_alain.py --discovery # مرة واحدة: إنشاء الكيانات في Home Assistant
python3 prayer_alain.py             # نشر المواقيت
```

أو استخدم سكربت التثبيت:

```bash
MODE=cron ./install.sh       # ينقل الملفات إلى /opt/awqaf-prayer ويضيف cron كل 5 دقائق
MODE=systemd ./install.sh    # بديل: مؤقّت systemd كل 5 دقائق
```

---

## 2) الإعدادات

كل الإعدادات عبر متغيّرات بيئة أو ملف `config.env` بجانب السكربت. ترتيب الأولوية:
خيارات سطر الأوامر ← متغيّرات البيئة ← `config.env` ← القيم الافتراضية.

| المتغيّر | الافتراضي | الوصف |
|---|---|---|
| `AWQAF_OFFICIAL_ID` | `37` | معرّف المدينة الرسمي (37 = أبوظبي - العين) |
| `AWQAF_CITY` | `مدينة العين` | اسم المدينة في اسم الجهاز داخل Home Assistant |
| `AWQAF_API_KEY` | مفتاح الواجهة العام | لتجاوز المفتاح الافتراضي |
| `AWQAF_TZ` | `Asia/Dubai` | المنطقة الزمنية لحساب الصلاة القادمة |
| `MQTT_HOST` | `localhost` | عنوان وسيط MQTT |
| `MQTT_PORT` | `1883` | منفذ الوسيط |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | — | بيانات دخول الوسيط إن كان محمياً |
| `MQTT_TOPIC_PREFIX` | `homeassistant/sensor/prayer_alain` | بادئة مواضيع الحالة |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | بادئة Discovery |
| `MQTT_RETAIN` | `true` | نشر محفوظ (retained) |
| `MQTT_CLIENT_ID` | `awqaf-prayer-<hostname>` | معرّف الاتصال |
| `MQTT_TIMEOUT` | `15` | مهلة الاتصال بالثواني |

---

## 3) أوامر الاستخدام

```bash
python3 prayer_alain.py                  # نشر المواقيت والعدّاد
python3 prayer_alain.py --dry-run        # عرض القيم دون نشر (للاختبار)
python3 prayer_alain.py --discovery      # نشر إعدادات الكيانات (retained)
python3 prayer_alain.py --list-locations # كل المدن ومعرّفاتها الرسمية
python3 prayer_alain.py --verbose        # طباعة كل رسالة MQTT
```

---

## 4) المواضيع والكيانات

| الموضوع `homeassistant/sensor/prayer_alain_*/state` | المحتوى | مثال |
|---|---|---|
| `fajr` | الفجر | `05:13` |
| `sunrise` | الشروق | `06:28` |
| `dhuhr` | الظهر | `12:05` |
| `asr` | العصر | `15:10` |
| `maghrib` | المغرب | `17:34` |
| `isha` | العشاء | `18:50` |
| `next_prayer` | الصلاة القادمة | `الفجر (غداً) في 05:14 (متبقي 6س و 58د)` |
| `date_hijri` | التاريخ الهجري | `6 جُمَادَى الآخِرَة 1448 هـ` |
| `date_gregorian` | التاريخ الميلادي | `16-11-2026` |

كل مستشعر يحمل `unique_id = uae_prayer_<key>` وجهازاً واحداً `uae_prayer_scraper`،
لذلك لا تتضاعف الكيانات عند إعادة النشر.

---

## 5) مدينة أخرى

```bash
python3 prayer_alain.py --list-locations | less     # اختر مدينتك
AWQAF_OFFICIAL_ID=36 AWQAF_CITY="مدينة أبوظبي" MQTT_TOPIC_PREFIX=homeassistant/sensor/prayer_abudhabi \
  python3 prayer_alain.py --discovery
```

مهم: غيّر `MQTT_TOPIC_PREFIX` عند تشغيل أكثر من مدينة على نفس الوسيط، وإلا ستتشارك المواضيع.
كما يجب تغيير `MQTT_CLIENT_ID`.

---

## 6) الجدولة

**cron** (كل 5 دقائق — يكفي لتحديث العدّاد):

```cron
*/5 * * * * cd /opt/awqaf-prayer && /usr/bin/python3 prayer_alain.py >> prayer.log 2>&1
```

**systemd**:

```bash
sudo cp systemd/awqaf-prayer.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now awqaf-prayer.timer
systemctl list-timers awqaf-prayer.timer
```

---

## 7) استكشاف الأخطاء

| الرسالة | السبب والحل |
|---|---|
| `ERROR: أخطاء من الواجهة` | تغيّر معرّف المدينة أو المفتاح. جرّب `--list-locations`. |
| `فشل الاتصال بـ MQTT (CONNACK)` | عنوان/منفذ خاطئ أو بيانات دخول ناقصة. |
| `Connection refused` | الوسيط متوقف أو `MQTT_HOST` غير صحيح. |
| الكيان لا يظهر في HA | شغّل `--discovery` وتحقق من إضافة MQTT في HA. |
| القيم قديمة | السكربت لم يُجدول أو فشل بصمت: راجع `prayer.log`. |
| العدّاد لا يتحدث | `AWQAF_TZ` غير مطابق لتوقيت السيرفر. |

---

## 8) تنبيهات

- البيانات ملك الهيئة العامة للشؤون الإسلامية والأوقاف، وهذا السكربت مجرد واجهة قراءة للاستخدام الشخصي.
- الواجهة غير موثّقة رسمياً للنشر العام، وقد تتغيّر دون إشعار.
- المفتاح `x-api-key` الافتراضي منشور أصلاً داخل كود موقع الأوقاف، ويمكن استبداله بمتغيّر بيئة.

## الترخيص

MIT — انظر `LICENSE`.
