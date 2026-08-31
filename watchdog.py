from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.trading_bot.telegram import enabled as telegram_enabled, send

ROOT = Path(__file__).resolve().parent
MAX_STALE_MINUTES = int(os.getenv('WATCHDOG_MAX_STALE_MINUTES', '35'))
MIN_EXPECTED_SUCCESS = int(os.getenv('WATCHDOG_MIN_EXPECTED_SUCCESS', '8'))


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def market_open():
    now = datetime.now(ZoneInfo('America/New_York'))
    minutes = now.hour * 60 + now.minute
    return now.weekday() < 5 and 570 <= minutes <= 960


def parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def main():
    state_path = ROOT / 'data/watchdog_state.json'
    health = load_json(ROOT / 'data/data_health.json', {})
    state = load_json(state_path, {})

    now = datetime.now(timezone.utc)
    is_open = market_open()
    last_scan = parse_ts(health.get('timestamp'))
    age_minutes = None if last_scan is None else (now - last_scan).total_seconds() / 60.0
    h = health.get('health') or {}
    success = int(h.get('success', 0) or 0)
    failures = int(h.get('failures', 0) or 0)
    total = success + failures
    success_ratio = (success / total) if total else 0.0

    reasons = []
    if is_open:
        if last_scan is None:
            reasons.append('لا يوجد سجل لفحص سوق ناجح')
        elif age_minutes > MAX_STALE_MINUTES:
            reasons.append(f'آخر فحص للسوق قبل {age_minutes:.0f} دقيقة')
        if health.get('status') != 'OK':
            reasons.append(f"حالة بيانات السوق: {health.get('status', 'UNKNOWN')}")
        if total and success < MIN_EXPECTED_SUCCESS:
            reasons.append(f'عدد مصادر/طلبات البيانات الناجحة منخفض: {success}')
        if total and success_ratio < 0.35:
            reasons.append(f'نسبة نجاح بيانات السوق منخفضة: {success_ratio:.0%}')

    unhealthy = bool(reasons)
    previous_unhealthy = bool(state.get('unhealthy'))

    if unhealthy and not previous_unhealthy:
        if not telegram_enabled():
            print('WATCHDOG_TELEGRAM_CONFIG_ERROR')
            return 3
        msg = [
            '🚨 <b>Trading Bot Watchdog</b>',
            'المراقبة أثناء جلسة السوق ليست سليمة حاليًا:',
            *[f'• {r}' for r in reasons[:4]],
            '',
            'لن أعتبر عدم وجود فرص نتيجة موثوقة حتى تعود الفحوصات للعمل.'
        ]
        send('\n'.join(msg))
        state['last_alert_at'] = now.isoformat()

    elif not unhealthy and previous_unhealthy and is_open:
        if telegram_enabled():
            age_text = f'{age_minutes:.0f} دقيقة' if age_minutes is not None else 'غير معروف'
            send(
                '✅ <b>Trading Bot Watchdog — Recovery</b>\n'
                f'المراقبة عادت للعمل بشكل طبيعي.\n'
                f'آخر فحص: قبل {age_text}\n'
                f'بيانات السوق الناجحة: {success} | الفاشلة: {failures}'
            )
            state['last_recovery_at'] = now.isoformat()

    state.update({
        'checked_at': now.isoformat(),
        'market_open': is_open,
        'unhealthy': unhealthy,
        'reasons': reasons,
        'last_scan_at': health.get('timestamp'),
        'last_scan_age_minutes': None if age_minutes is None else round(age_minutes, 1),
        'market_data_success': success,
        'market_data_failures': failures,
        'market_data_success_ratio': round(success_ratio, 4) if total else None,
    })
    save_json(state_path, state)

    print(
        f"Watchdog: market_open={is_open} unhealthy={unhealthy} "
        f"last_scan_age={state['last_scan_age_minutes']}m success={success} failures={failures}"
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
