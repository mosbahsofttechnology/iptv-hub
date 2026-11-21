# 📡 IPTV Hub — ایران و افغانستان

یک سیستم خودکار برای نگهداری لینک‌های IPTV:

- کانال‌های ایران و افغانستان را از [iptv-org](https://github.com/iptv-org/iptv) می‌گیرد
- لینک‌های دستی (مثل `1TV`) را از `data/overrides.json` اضافه می‌کند
- سلامت واقعی هر لینک را چک می‌کند: **master playlist → media playlist → دانلود سگمنت ویدیو**
- وضعیت را در SQLite نگه می‌دارد و یک اسنپ‌شات JSON قابل‌کامیت می‌سازد
- پلی‌لیست، `status.json` و داشبورد HTML تولید می‌کند
- با GitHub Actions هر ۱۲ ساعت خودکار اجرا و به‌روز می‌شود

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
| `scripts/dashboard_template.html` | قالب داشبورد |
| `data/overrides.json` | لینک‌های دستی و اولویت‌دار |
| `data/state.json` | اسنپ‌شات پایدار وضعیت (کامیت می‌شود) |
| `data/iptv.db` | دیتابیس محلی SQLite (کامیت نمی‌شود) |
| `playlist.m3u` | همه لینک‌های فعال (شامل لینک‌های دارای محدودیت جغرافیایی) |
| `playlist-verified.m3u` | فقط لینک‌هایی که آخرین چک «سالم» بوده‌اند |
| `playlists/iran.m3u` | فقط ایران |
| `playlists/afghanistan.m3u` | فقط افغانستان |
| `status.json` | وضعیت کامل همه کانال‌ها و لینک‌ها برای اپ/API |
| `docs/index.html` | داشبورد استاتیک (GitHub Pages) |

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
git remote add origin https://github.com/USERNAME/iptv-hub.git
git push -u origin main
```

3. در GitHub بروید به **Settings → Actions → General → Workflow permissions** و گزینه **Read and write permissions** را انتخاب کنید (لازم است چون ربات باید نتیجه را commit کند).
4. از تب **Actions** ورک‌فلوی **Update IPTV links** را باز کنید و یک بار **Run workflow** بزنید.
5. برای داشبورد: **Settings → Pages → Source: Deploy from a branch → Branch: main / folder: /docs**.
6. لینک‌های ثابت شما:

```
https://raw.githubusercontent.com/USERNAME/iptv-hub/main/playlist.m3u
https://raw.githubusercontent.com/USERNAME/iptv-hub/main/playlist-verified.m3u
https://USERNAME.github.io/iptv-hub/
```

از این به بعد هر ۱۲ ساعت خودکار اجرا می‌شود. با `workflow_dispatch` هم هر وقت خواستید دستی اجرا کنید.

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

قوانین:

- **`ok`**: لینک سالم؛ `fail_streak = 0` و `last_ok_at` به‌روز می‌شود.
- **`geo` (403/451)**: ممکن است فقط از ایران/افغانستان کار کند؛ هرگز خودکار حذف نمی‌شود.
- **`dead` (404/410/DNS/refused)**: بعد از **۳ خطای پشت‌سرهم** غیرفعال می‌شود.
- **`unstable` (timeout/5xx/playlist ناقص)**: فعال می‌ماند چون ممکن است موقتی باشد.
- لینک غیرفعال هم در اجرای بعدی دوباره چک می‌شود و در صورت بازگشت خودکار فعال می‌شود.

> چون GitHub Actions از سرورهای خارج از ایران اجرا می‌شود، بعضی شبکه‌های ایرانی `403` می‌دهند. برای همین `playlist.m3u` آن‌ها را نگه می‌دارد و `playlist-verified.m3u` فقط لینک‌های تأییدشده از سرور خارج را می‌دهد.

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
