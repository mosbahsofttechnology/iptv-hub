# 📡 IPTV Hub — ایران و افغانستان

یک سیستم خودکار برای نگهداری لینک‌های IPTV:

- کانال‌های ایران و افغانستان را از [iptv-org](https://github.com/iptv-org/iptv) می‌گیرد
- لینک‌های دستی (مثل `1TV`) را از `data/overrides.json` اضافه می‌کند
- سلامت واقعی هر لینک را چک می‌کند: **master playlist → media playlist → دانلود سگمنت ویدیو**
- وضعیت را در SQLite نگه می‌دارد و یک اسنپ‌شات JSON قابل‌کامیت می‌سازد
- پلی‌لیست، `status.json` و داشبورد HTML تولید می‌کند
- با GitHub Actions هر ۱۲ ساعت خودکار اجرا و به‌روز می‌شود
- دو پروکسی (ایران/خارج) مسیر پخش هر کانال را بر اساس geo و CORS انتخاب می‌کنند

**داشبورد اصلی:** `https://mosbahsofttechnology.github.io/iptv-hub/`
**داشبورد داخلی (سرور ایران):** `https://stream.cinimo.ir/iptv-dashboard/`
**داشبورد آینه (سرور خارج):** `https://api.polpeisa.arianadevs.com/iptv-dashboard/`

**پلی‌لیست مسیر ایران / خارج:** `playlist-ir.m3u` / `playlist-intl.m3u`

```
iptv-org (ir/af)  +  data/overrides.json
              ↓
      SQLite (data/iptv.db، محلی)
              ↓  health-check سه‌مرحله‌ای
   بهترین لینک سالم هر کانال
              ↓
playlist.m3u / playlist-verified.m3u / status.json / docs/index.html
```

## فایل‌ها

| مسیر | توضیح |
|---|---|
| `scripts/update.py` | کل پایپ‌لاین؛ بدون وابستگی خارجی (فقط کتابخانه استاندارد پایتون) |
| `scripts/update-and-push.sh` | اجرای محلی + commit و push (پشتیبان Actions) |
| `scripts/dashboard_template.html` | قالب داشبورد |
| `data/overrides.json` | لینک‌های دستی و اولویت‌دار |
| `data/state.json` | اسنپ‌شات پایدار وضعیت (کامیت می‌شود) |
| `data/iptv.db` | دیتابیس محلی SQLite (کامیت نمی‌شود) |
| `playlist.m3u` | همه لینک‌های فعال (لینک اصلی هر کانال) |
| `playlist-verified.m3u` | فقط لینک‌هایی که آخرین چک «سالم» بوده‌اند |
| `playlist-ir.m3u` | لینک‌های تمیز `/st/<id>` از مسیر ایران |
| `playlist-intl.m3u` | لینک‌های تمیز `/st/<id>` از مسیر خارج |
| `playlists/iran.m3u` | فقط ایران |
| `playlists/afghanistan.m3u` | فقط افغانستان |
| `status.json` | وضعیت کامل همه کانال‌ها و لینک‌ها برای اپ/API |
| `docs/index.html` | داشبورد استاتیک (GitHub Pages) |
| `docs/hls.min.js` | پلیر HLS داخل داشبورد (hls.js، MIT) |
| `proxy/server.py` | پروکسی HLS/CORS بدون وابستگی برای دو سرور |
| `proxy/README.md` | معماری، نصب systemd/nginx و نگهداری پروکسی |

## راه‌اندازی روی GitHub

1. یک ریپوی **عمومی** بسازید (مثلاً `iptv-hub`).
2. این پوشه را push کنید:

```bash
cd iptv-hub
git init -b main
git add .
git commit -m "init: IPTV hub"
gh repo create iptv-hub --public --source=. --push
```

یا بدون `gh`: ریپو را در GitHub بسازید و:

```bash
git remote add origin https://github.com/mosbahsofttechnology/iptv-hub.git
git push -u origin main
```

3. در GitHub بروید به **Settings → Actions → General → Workflow permissions** و گزینه **Read and write permissions** را انتخاب کنید (لازم است چون ربات باید نتیجه را commit کند).
4. از تب **Actions** ورک‌فلوی **Update IPTV links** را باز کنید و یک بار **Run workflow** بزنید.
5. برای داشبورد: **Settings → Pages → Source: Deploy from a branch → Branch: main / folder: /docs**.
6. لینک‌های ثابت شما:

```
https://raw.githubusercontent.com/mosbahsofttechnology/iptv-hub/main/playlist.m3u
https://raw.githubusercontent.com/mosbahsofttechnology/iptv-hub/main/playlist-verified.m3u
https://mosbahsofttechnology.github.io/iptv-hub/
```

از این به بعد هر ۱۲ ساعت خودکار اجرا می‌شود. با `workflow_dispatch` هم هر وقت خواستید دستی اجرا کنید.

## اگر GitHub Actions اجرا نشد

اگر در تب Actions این خطا را دیدید:

> The job was not started because your account is locked due to a billing issue.

یعنی حساب GitHub از نظر Billing قفل است، نه اینکه کد یا ریپو مشکل داشته باشد. حتی برای ریپوی عمومی رایگان، GitHub تا حل‌شدن وضعیت پرداخت اجازه اجرا نمی‌دهد.

1. به `https://github.com/settings/billing` بروید و وضعیت پرداخت/روش پرداخت را درست کنید.
2. بعد از رفع قفل، از تب Actions دوباره **Run workflow** بزنید.

تا آن زمان، به‌روزرسانی محلی + push:

```bash
chmod +x scripts/update-and-push.sh
./scripts/update-and-push.sh
```

یا با cron هر روز صبح (مسیر را با مسیر واقعی پروژه عوض کنید):

```cron
0 4 * * * cd /Users/abbasnazari/Documents/Default\ Project/iptv-hub && ./scripts/update-and-push.sh >> /tmp/iptv-hub.log 2>&1
```

داشبورد Pages چون فایل استاتیک است حتی بدون اجرای Actions هم از آخرین commit سرو می‌شود.

## اجرای محلی

نیازی به نصب چیزی نیست:

```bash
# ساخت سریع دیتابیس و پلی‌لیست بدون چک سلامت
python3 scripts/update.py --no-check

# چک کامل (چند دقیقه طول می‌کشد)
python3 scripts/update.py --workers 24

# تست سریع فقط ۱۰ لینک
python3 scripts/update.py --limit 10
```

خروجی‌ها در همان پوشه ساخته می‌شوند. دیتابیس را می‌توانید با `sqlite3 data/iptv.db` ببینید:

```sql
SELECT c.name, s.url, s.status, s.latency_ms
FROM channels c JOIN streams s ON s.channel_id = c.id
WHERE s.status = 'ok'
ORDER BY c.name LIMIT 20;
```

## افزودن شبکه دستی

در `data/overrides.json`:

```json
[
  {
    "channel_id": "1TV.af",
    "name": "1TV Afghanistan",
    "country": "AF",
    "category": "General",
    "url": "https://example.com/live/1tv.m3u8",
    "quality": "1080p",
    "priority": 100,
    "referrer": ""
  }
]
```

- `channel_id`: اگر با `tvg-id` همان کانال در iptv-org یکی باشد، لینک دستی به همان کانال وصل می‌شود.
- `priority`: بالاتر = در انتخاب «بهترین لینک» جلوتر.
- اگر URL یک کانال را عوض کنید، لینک دستی قبلی خودکار غیرفعال می‌شود.
- برای استریم‌هایی که Referer می‌خواهند، `referrer` را پر کنید.

## منطق چک سلامت

فقط `200 OK` کافی نیست؛ خیلی از لینک‌ها پلی‌لیست می‌دهند ولی سگمنت ویدیو پخش نمی‌شود. بنابراین:

1. `GET` روی لینک اصلی با `Range` و timeout
2. اگر master playlist بود، بهترین variant را انتخاب می‌کند
3. اولین سگمنت ویدیو را دانلود می‌کند و نوع محتوا را بررسی می‌کند
4. نتیجه ذخیره می‌شود: `ok` / `geo` / `dead` / `unstable` / `unknown`
5. برای لینک‌های `ok`، جداگانه تست می‌شود که مرورگر هم بتواند پخش کند (`browser_ok`)

قوانین:

- **`ok`**: لینک سالم؛ `fail_streak = 0` و `last_ok_at` به‌روز می‌شود.
- **`geo` (403/451)**: ممکن است فقط از ایران/افغانستان کار کند؛ هرگز خودکار حذف نمی‌شود.
- **`dead` (404/410/DNS/refused)**: بعد از **۳ خطای پشت‌سرهم** غیرفعال می‌شود.
- **`unstable` (timeout/5xx/playlist ناقص)**: فعال می‌ماند چون ممکن است موقتی باشد.
- لینک غیرفعال هم در اجرای بعدی دوباره چک می‌شود و در صورت بازگشت خودکار فعال می‌شود.

> چون GitHub Actions از سرورهای خارج از ایران اجرا می‌شود، بعضی شبکه‌های ایرانی `403` می‌دهند. برای همین `playlist.m3u` آن‌ها را نگه می‌دارد و `playlist-verified.m3u` فقط لینک‌های تأییدشده از سرور خارج را می‌دهد.

## چرا بعضی لینک‌های «سالم» در مرورگر پخش نمی‌شوند؟

«سالم» یعنی سرور، پلی‌لیست و سگمنت را به GitHub می‌دهد. ولی مرورگر برای پخش HLS
با `hls.js`، همه‌ی این فایل‌ها را با XHR می‌گیرد و سرور باید
`Access-Control-Allow-Origin` بدهد. خیلی از سرورهای IPTV فقط به سایت خودشان
اجازه می‌دهند (مثلاً `telewebion.ir`) و بقیه را بلاک می‌کنند. VLC چنین محدودیتی
ندارد، بنابراین همان لینک در VLC پخش می‌شود.

برای شفافیت، هر لینک `ok` یک فیلد `browser_ok` هم دارد:

- `browser_ok = true`: در داشبورد بدون پروکسی و با CORS باز می‌شود.
- `browser_ok = false`: در مرورگر CORS/HTTP مانع است؛ از پروکسی یا VLC/IINA استفاده کنید.

## مسیریابی دو سروره (ایران + خارج)

دو سرویس پروکسی یکسان روی دو سرور اجرا می‌شوند:

| مسیر | نقش |
|---|---|
| `https://stream.cinimo.ir/iptv-hls` | سرور ایران؛ کانال‌هایی که از خارج در دسترس نیستند و رفع CORS |
| `https://api.polpeisa.arianadevs.com/iptv-hls` | سرور خارج؛ کانال‌های خارجی که داخل ایران فیلتر/محدودند |

برای هر کانال، `status.json` یک آبجکت `proxy` دارد:

```json
"proxy": {
  "ir": "https://stream.cinimo.ir/iptv-hls/p/<token>",
  "intl": "https://api.polpeisa.arianadevs.com/iptv-hls/p/<token>"
},
"reach": { "ir": true, "intl": false }
```

داشبورد بر اساس `status`، `reach` و منطقه کاربر (ایران/خارج/خودکار) مسیر را
انتخاب می‌کند:

- کانال `geo` یا فقط قابل دسترس از ایران → پروکسی ایران.
- کانال خارجی برای کاربر ایران → پروکسی خارج.
- کانال خارجی برای کاربر خارج و دارای CORS → مستقیم.
- اگر مسیر اول جواب نداد، مسیر بعدی امتحان می‌شود.

`reach.ir` و `reach.intl` برای کانال‌های `geo` با probe واقعی از هر دو پروکسی
پر می‌شوند. اگر `reach.intl` درست باشد، برچسب «مسیر خارج» می‌گیرد و از سرور
خارج پخش می‌شود؛ اگر `reach.ir` درست باشد، «مسیر ایران»؛ و اگر هر دو `false`
باشند، «غیرقابل دسترس».

نصب، امنیت و راه‌اندازی پروکسی در [`proxy/README.md`](proxy/README.md) است.
اگر پروکسی تنظیم نشده باشد، داشبورد به همان پخش مستقیم/VLC برمی‌گردد.

### منبع دوم: Pars TV

برای شبکه‌های ایرانی/افغان، لینک‌های HLS از صفحه‌های
[`parsatv.com`](https://www.parsatv.com/) هم استخراج و در `data/overrides.json`
ذخیره شده‌اند. این لینک‌ها به‌عنوان گزینه‌های اضافی (priority پایین) تست می‌شوند؛
اگر لینک فعلی یک شبکه از کار بیفتد، نسخه Pars TV جای آن را می‌گیرد.
بیشتر این استریم‌ها روی `wns.live` / `persiana.live` / CloudFront هستند و از
هر دو سرور قابل تست‌اند. اگر لینکی از GitHub `geo` گزارش شود ولی پروکسی خارج
آن را بگیرد، برچسب «مسیر خارج» می‌گیرد و از همان مسیر پخش می‌شود.

### GEM و Manoto

- **GEM**: لینک‌های iptv-org روی CDN خصوصی `onetv.app` هستند و از دیتاسنتر
  ایران/خارج در دسترس نیستند. لینک GEM Food از Aparatchii (`gg.hls2.xyz`) هم
  برای دیتاسنترها 403 می‌دهد. داشبورد برای این منابع **اول پخش مستقیم** را
  امتحان می‌کند؛ اگر شبکه/اپ OneTV یا IP شما اجازه بدهد پخش می‌شود، وگرنه
  پیام «منبع خصوصی» می‌گیرد.
- **Manoto**: کانال در مه ۲۰۲۶ بسته شده و لینک‌های عمومی قدیمی
  (Akamai/CloudFront) دیگر DNS ندارند. لینک زندهٔ عمومی معتبری پیدا نشد.
  اگر لینک تازه‌ای دارید، در `data/overrides.json` اضافه کنید.

### انتشار در اپ آسمان تی وی (Goshimi)

اپ آسمان تی وی (`com.asemantv.app.aseman_tv`) کاتالوگش را در فیلدهای
`apps.extra` نگه می‌دارد:

`tvs_afghanistan`، `tvs_iran_internal`، `tvs_iran_satellite`،
`radios_afghanistan`، `radios_iran`.

برای آپدیت امن، روی همان بکند Goshimi دو اکشن به پنل API اضافه شده است:

```text
POST https://apps.goshimi.ir/admin/api.php
Authorization: Bearer <GOSHIMI_API_KEY>

{"action":"fields_get"}
{"action":"fields_set","fields":{...}}
```

- `fields_get` فیلدهای فعلی را برمی‌گرداند.
- `fields_set` فقط فیلدهای مجاز کاتالوگ را **merge** می‌کند
  (health probe، auto-switch و بقیهٔ تنظیمات دست نمی‌خورند).
- کلید API باید package-scoped برای همان اپ باشد.

اسکریپت [`scripts/publish_goshimi.py`](scripts/publish_goshimi.py):

1. `status.json` را می‌خواند.
2. کانال‌های هر آیتم Goshimi را با `tvgId`/شناسهٔ کانال تطبیق می‌دهد.
3. به منابع هر آیتم، لینک تمیز مسیر مناسب را اضافه می‌کند:
   - داخلی: `/st` ایران
   - ماهواره‌ای: `/st` خارج
   - افغانستان: مستقیم + `/st`
   - GEM/Manoto: منبع خصوصی OneTV (برای اپ بومی)
4. عنوان/لوگو/دسته/رادیوها و سورس‌های قبلی را حفظ می‌کند.
5. `catalog_version` را یک واحد بالا می‌برد تا اپ کاتالوگ تازه را بگیرد.

اجرا:

- **آینه خارج** بعد از هر آپدیت (`run.sh`) خودکار اجرا می‌کند؛ کلید در
  `/opt/iptv-hub/.env` است (فقط root).
- **GitHub Actions** یک استپ اختیاری دارد؛ کلید را در
  Settings → Secrets and variables → Actions با نام `GOSHIMI_API_KEY`
  بگذارید. اگر نباشد، استپ رد می‌شود و آینه کار را انجام می‌دهد.

> پچ API و بکاپ‌ها روی سرور ایران در
> `/www/wwwroot/apps.goshimi.ir/admin/api.php` و
> `/root/goshimi-backups/` هستند. اگر پنل Goshimi را آپدیت کردید،
> اکشن‌های `fields_get`/`fields_set` را دوباره اضافه کنید.
> رادیوها چون در منبع ما نیستند دست‌نخورده می‌مانند.

### لینک تمیز `/st/<channel-id>`

هر کانال در `status.json` سه لینک دارد:

```json
"links": {
  "direct": "https://fflive-darya-educationtv.b-cdn.net/master.m3u8",
  "ir":     "https://stream.cinimo.ir/st/DatisTV.ir",
  "intl":   "https://api.polpeisa.arianadevs.com/st/DatisTV.ir"
}
```

- `direct` همان آدرس اصلی است.
- `ir` از سرور ایران و `intl` از سرور خارج رد می‌شود.
- این پیشوند **خود پروکسی** است؛ سرور فایل را از مبدأ می‌گیرد و به مرورگر می‌دهد. برای استریم geo-بلاک/فیلترشده چاره‌ای جز عبور از یک سرور در منطقهٔ درست نیست.
- برای امنیت، `st/` آدرس خام را قبول نمی‌کند (پروکسی باز می‌شود)؛ فقط `channel-id`های موجود در نقشهٔ کانال‌ها را می‌پذیرد و زیرلینک‌ها را با توکن کوتاه امضا می‌کند.
- `playlist-ir.m3u` و `playlist-intl.m3u` همین لینک‌ها را برای VLC/اپ می‌دهند.

### داشبورد آینه و توکن‌ها

دو آینه‌ی استاتیک هم سرو می‌شود:

```
https://stream.cinimo.ir/iptv-dashboard/                  (سرور ایران)
https://api.polpeisa.arianadevs.com/iptv-dashboard/       (سرور خارج)
```

یک cron روی سرور خارج (ساعت ۰۳:۱۷ و ۱۵:۱۷ UTC) چک سلامت و توکن‌ها را
به‌روز می‌کند و `docs/index.html` و `docs/status.json` را بازمی‌سازد.
سرور ایران هر ۳۰ دقیقه همان دو فایل را از آینه‌ی خارج می‌گیرد تا کاربران
داخل ایران یک داشبورد داخلی و سریع داشته باشند.
GitHub Actions هم اگر `IPTV_PROXY_SECRET` تنظیم نشده باشد، توکن‌ها و
`reach` را از همین `status.json` عمومی می‌خواند (`IPTV_MIRROR_STATUS_URL`)
و در نسخه Pages استفاده می‌کند. بنابراین حتی بدون Secret در GitHub هم
داشبورد Pages لینک‌های پروکسی دارد. اگر آینه در دسترس نباشد، آخرین نسخه
کش‌شده در `data/mirror-status.json` استفاده می‌شود.

## هزینه GitHub Actions

| مورد | وضعیت |
|---|---|
| ریپوی عمومی | رانرهای استاندارد رایگان و بدون محدودیت دقیقه |
| ریپوی خصوصی (Free) | حدود ۲٬۰۰۰ دقیقه در ماه |
| مصرف این پروژه | ۲ اجرا در روز، هر اجرا چند دقیقه → حدود ۳۰۰ دقیقه در ماه |
| GitHub Pages | برای ریپوی عمومی رایگان |

نکته‌ها:

- زمان‌بندی GitHub دقیق نیست و ممکن است چند دقیقه تا نیم‌ساعت تأخیر داشته باشد.
- اگر ریپو ۶۰ روز فعالیت نداشته باشد، GitHub اجرای زمان‌بندی‌شده را غیرفعال می‌کند؛ برای همین ورک‌فلو در صورت نبود تغییر، هر ۳۰ روز یک heartbeat کوچک commit می‌کند.
- برای چک دقیق‌تر شبکه‌های ایرانی می‌توانید یک **self-hosted runner** با IP ایران اضافه کنید (در README توسعه آمده).

## چرا SQLite در گیت کامیت نمی‌شود؟

فایل باینری SQLite با هر اجرا تغییر می‌کند و تاریخچه گیت را سنگین می‌کند. به‌جای آن:

- `data/state.json` به‌عنوان اسنپ‌شات قابل‌diff کامیت می‌شود
- هر اجرا دیتابیس SQLite را از روی آن + منابع دوباره می‌سازد
- خود دیتابیس برای کوئری محلی در `data/iptv.db` می‌ماند

اگر اصرار دارید خود دیتابیس هم در ریپو باشد، کافی است `data/iptv.db` را از `.gitignore` بردارید.

## توسعه‌های بعدی

- **EPG**: افزودن راهنمای برنامه به پلی‌لیست
- **چند منبع**: اضافه‌کردن پلی‌لیست‌های فارسی/پشتو و منابع محلی
- **Threadfin**: پروکسی M3U با failover خودکار بین چند لینک
- **تلگرام/ربات**: اطلاع‌رسانی وقتی شبکه‌ای قطع یا وصل می‌شود
- **self-hosted runner ایران**: چک دقیق‌تر لینک‌های geo-blocked

## نکته حقوقی

این پروژه فقط لینک استریم‌های عمومی (Free-to-Air) را جمع‌آوری و وضعیتشان را بررسی می‌کند؛ هیچ ویدیویی ذخیره یا بازپخش نمی‌شود. فقط محتوایی را تماشا کنید که اجازه دیدنش را دارید.
