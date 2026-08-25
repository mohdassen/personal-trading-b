# Personal US Trading Bot

بوت شخصي لتحليل الأسهم الأمريكية وإرسال فرص التداول، مع تنفيذ الشراء والبيع يدويًا في منصة Sahm.

## ما الذي يفعله؟
- يراقب قائمة أسهم محددة في `config/watchlist.yml`.
- يجلب بيانات 15 دقيقة من Yahoo Finance عبر `yfinance`.
- يحسب EMA 9/21/50 وRSI وVWAP وATR وحجم التداول والزخم.
- يعطي Score من 100 وإشارة `BUY / WATCH / WAIT`.
- يحسب نطاق دخول، Stop Loss، Target 1/2، والكمية المقترحة حسب مخاطرة الحساب.
- يولد Dashboard عربي Static في `docs/index.html`.
- يرسل أفضل إشارات BUY إلى Telegram عند إعداد الأسرار.
- يعمل مجدولًا عبر GitHub Actions.

## تنبيه مهم
هذه الأداة للتحليل والمساعدة فقط وليست توصية مالية. بيانات Yahoo Finance قد تكون متأخرة أو غير مناسبة للتنفيذ اللحظي. قارن السعر دائمًا مع Sahm قبل تنفيذ أي أمر.

## تشغيل محلي
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python run.py
```
ثم افتح `docs/index.html`.

## إعداد GitHub
1. أنشئ Repository وارفع الملفات إليه.
2. من `Settings > Pages` اختر **GitHub Actions** كمصدر النشر.
3. من `Settings > Secrets and variables > Actions` أضف عند الحاجة:
   - Secret: `TELEGRAM_BOT_TOKEN`
   - Secret: `TELEGRAM_CHAT_ID`
4. من تبويب **Variables** يفضل إضافة:
   - `ACCOUNT_EQUITY_USD` — رأس المال الذي تريد أن يستخدمه البوت لحساب حجم الصفقة.
   - `RISK_PER_TRADE_PCT` — مثال `0.5`.
   - `MAX_POSITION_PCT` — مثال `15`.
5. افتح `Actions > US Market Scan > Run workflow` لتجربة أول Scan يدويًا.

## إعداد Telegram
- أنشئ Bot عبر BotFather واحصل على Token.
- أرسل أي رسالة للبوت.
- استخرج Chat ID بالطريقة الرسمية المتاحة لك، ثم خزنه كـSecret.
- لا تضع Token داخل الكود.

## الاستراتيجية الحالية
التقييم الافتراضي يجمع:
- اتجاه EMA حتى 25 نقطة.
- السعر مقارنة بـVWAP حتى 15 نقطة.
- RSI حتى 20 نقطة.
- Volume Ratio حتى 20 نقطة.
- زخم ساعة حتى 20 نقطة.

`BUY >= 80`, و`WATCH >= 65`، والباقي `WAIT`.

## إدارة المخاطر
افتراضيًا:
- مخاطرة الصفقة: 0.5% من رأس المال.
- الحد الأقصى لقيمة المركز: 15% من رأس المال.
- Stop Loss مبني على ATR مع حد أدنى 1.5% تقريبًا.
- Target 1 عند R:R = 1:2 وTarget 2 عند 1:3.

## الخصوصية
لا يوجد أي ربط بحساب Sahm ولا تخزين لبيانات دخول Sahm. التنفيذ يدوي بالكامل.
