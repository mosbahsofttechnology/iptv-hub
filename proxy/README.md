# پروکسی دو سروره (ایران + خارج)

این پوشه سرویس کوچکی است که روی **دو سرور** اجرا می‌شود:

| سرور | نقش | دامنه/مسیر نمونه | پورت محلی |
|---|---|---|---|
| ایران | کانال‌هایی که از خارج ایران در دسترس نیستند (و رفع CORS) | `https://stream.cinimo.ir/iptv-hls` | `19080` |
| خارج | کانال‌های خارجی که داخل ایران فیلتر/محدود هستند | `https://api.polpeisa.arianadevs.com/iptv-hls` | `18080` |

هر دو سرویس یک `TOKEN_SECRET` مشترک دارند. آپدیت‌کننده (GitHub Actions) برای هر کانال
یک توکن امضاشده می‌سازد و آدرس `/p/<token>` را در `status.json` می‌گذارد. داشبورد
بر اساس وضعیت کانال و منطقه کاربر یکی از این دو مسیر را انتخاب می‌کند.

از نسخهٔ جدید، لینک تمیز `/st/<channel-id>` هم هست:

```
https://stream.cinimo.ir/st/DatisTV.ir     (مسیر ایران)
https://api.polpeisa.arianadevs.com/st/DatisTV.ir  (مسیر خارج)
```

این مسیر از `CHANNEL_MAP_URL` (پیش‌فرض: `https://api.polpeisa.arianadevs.com/iptv-dashboard/status.json`)
نقشهٔ کانال‌ها را هر ۵ دقیقه می‌خواند؛ فقط `channel-id`های موجود را می‌پذیرد و
زیرلینک‌ها را با توکن کوتاه امضا می‌کند. `PLAY_TOKEN_TTL` پیش‌فرض ۴۸ ساعت است.

```
کانال داخلی (geo)  ──> پروکسی ایران
کانال خارجی (ok)   ──> پروکسی خارج (برای کاربر ایران)
                     └─ مستقیم (برای کاربر خارج، اگر CORS بدهد)
```

## چرا دو سرور؟

- سرور ایران می‌تواند استریم‌هایی را بگیرد که IP خارجی را بلاک می‌کنند.
- سرور خارج می‌تواند استریم‌هایی را بگیرد که از ایران فیلتر/محدود هستند.
- پروکسی علاوه بر geo، مشکل CORS را هم حل می‌کند: پلی‌لیست و سگمنت‌ها با
  `Access-Control-Allow-Origin: *` به مرورگر می‌رسند.

## ساختار

| فایل | توضیح |
|---|---|
| `server.py` | سرویس پایتون بدون وابستگی خارجی (فقط stdlib) |
| `iptv-proxy.service` | یونیت systemd |
| `nginx-location.conf` | بلوک nginx برای مسیر `/iptv-hls/` |

## نصب روی هر سرور

پیش‌نیاز: Ubuntu/Debian با Python 3.10+ و nginx.

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin iptvproxy
sudo mkdir -p /opt/iptv-proxy
sudo cp server.py /opt/iptv-proxy/server.py
sudo cp iptv-proxy.service /etc/systemd/system/iptv-proxy.service
sudo chmod 644 /opt/iptv-proxy/server.py
```

فایل `/opt/iptv-proxy/.env` را بسازید (فقط root بخواند):

```env
PORT=19080
PUBLIC_BASE_URL=https://stream.cinimo.ir/iptv-hls
TOKEN_SECRET=<همان رشته تصادفی روی هر دو سرور>
REGION=ir
BLOCK_PRIVATE_HOSTS=0
UPSTREAM_TIMEOUT=25
MAX_CONCURRENT=96
SUB_TOKEN_TTL=3600
INSECURE_UPSTREAM=1
CHANNEL_MAP_URL=https://api.polpeisa.arianadevs.com/iptv-dashboard/status.json
PLAY_TOKEN_TTL=172800
```

روی سرور خارج:

```env
PORT=18080
PUBLIC_BASE_URL=https://api.polpeisa.arianadevs.com/iptv-hls
TOKEN_SECRET=<همان رشته>
REGION=intl
BLOCK_PRIVATE_HOSTS=1
```

سپس:

```bash
sudo chmod 600 /opt/iptv-proxy/.env
sudo systemctl daemon-reload
sudo systemctl enable --now iptv-proxy
curl -sS http://127.0.0.1:19080/health
```

### nginx

بلوک `nginx-location.conf` را داخل `server { listen 443 ... }` همان دامنه
قرار دهید و `__PORT__` را با پورت همان سرور عوض کنید. قبل از reload:

```bash
sudo nginx -t && sudo nginx -s reload
```

سپس:

```bash
curl -sS https://stream.cinimo.ir/iptv-hls/health
```

> در این نصب، vhostهای هر دو سرور توسط پنل (aaPanel) مدیریت می‌شوند. اگر پنل
> کانفیگ را بازسازی کرد، بلوک `location ^~ /iptv-hls/` را دوباره اضافه کنید.
> از فایل کانفیگ قبل از تغییر backup بگیرید.

## امنیت

- بدون توکن، پروکسی هیچ آدرسی را fetch نمی‌کند؛ توکن شامل خود URL و تاریخ انقضاست
  و با HMAC-SHA256 امضا شده است. کاربر نمی‌تواند URL را عوض کند.
- `TOKEN_SECRET` را در ریپو یا جای عمومی نگذارید. فقط در GitHub Actions Secret
  و `/opt/iptv-proxy/.env` دو سرور است.
- روی سرور خارج `BLOCK_PRIVATE_HOSTS=1` بماند. روی سرور ایران برای برخی CDNهای
  داخلی که DNS خصوصی دارند `0` گذاشته شده؛ چون توکن‌ها URL-scoped هستند خطر
  SSRF محدود است.
- `MAX_CONCURRENT` سقف اتصال هم‌زمان است. برای جلوگیری از مصرف بی‌رویه،
  rate limit در nginx/Cloudflare یا توکن کوتاه‌تر هم اضافه کنید.
- توکن‌ها ۴۸ ساعت اعتبار دارند و هر اجرای Actions تازه می‌شوند.

## اتصال به GitHub Actions

در تنظیمات ریپو:

- Variable `IPTV_PROXY_IR_URL` = `https://stream.cinimo.ir/iptv-hls`
- Variable `IPTV_PROXY_INTL_URL` = `https://api.polpeisa.arianadevs.com/iptv-hls`
- Secret `IPTV_PROXY_SECRET` = همان `TOKEN_SECRET`

ورک‌فلو این‌ها را به `scripts/update.py` می‌دهد. اسکریپت:

1. برای هر کانال `ok/geo/unstable` آدرس `proxy.ir` و `proxy.intl` می‌سازد.
2. برای کانال‌های `geo` از پروکسی ایران می‌پرسد که واقعاً از ایران در دسترس‌اند
   (`/probe/<token>`) و نتیجه را در `reach.ir` می‌گذارد.
3. داشبورد با `reach` و `status` مسیر را انتخاب می‌کند.

## تست دستی

ساخت یک توکن (روی ماشین خودتان، با همان secret):

```bash
export IPTV_PROXY_SECRET='...'
python3 - <<'PY'
import base64, hashlib, hmac, json, os, time
secret = os.environ['IPTV_PROXY_SECRET']
url = 'https://ncdn.telewebion.ir/tv1/live/playlist.m3u8'
payload = {'v': 1, 'k': 'play', 'u': url, 'x': int(time.time()) + 600}
enc = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).rstrip(b'=').decode()
sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), enc.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
print(f'https://stream.cinimo.ir/iptv-hls/p/{enc}.{sig}')
PY
```

## محدودیت‌ها

- پهنای باند ویدیو روی سرور می‌افتد: هر بیننده 720p حدود ۱.۱ گیگابایت در ساعت.
- اگر سرور ایران به یک CDN داخلی دسترسی نداشته باشد (مثلاً DNS آن به IP خصوصی
  غیرقابل‌دسترس برسد)، پروکسی هم نمی‌تواند آن کانال را پخش کند.
- بعضی سرورها `Referer` خاص می‌خواهند؛ پروکسی `Referer` مبدأ را می‌فرستد.
- این سرویس برای استفاده شخصی/کوچک است؛ برای مخاطب زیاد به rate limit، CDN و
  ظرفیت پهنای باند بیشتری نیاز دارید.
