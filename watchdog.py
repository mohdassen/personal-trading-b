from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.trading_bot.telegram import enabled as telegram_enabled, send

ROOT = Path(__file__).resolve().parent
MAX_STALE_MINUTES = int(os.getenv('WATCHDOG_MAX_STALE_MINUTES', '25'))
MIN_EXPECTED_SUCCESS = int(os.getenv('WATCHDOG_MIN_EXPECTED_SUCCESS', '8'))
SELF_HEAL = os.getenv('WATCHDOG_SELF_HEAL', '1').lower() in ('1', 'true', 'yes')


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


def health_snapshot(now):
    health = load_json(ROOT / 'data/data_health.json', {})
    last_scan = parse_ts(health.get('timestamp'))
    age_minutes = None if last_scan is None else (now - last_scan).total_seconds() / 60.0
    h = health.get('health') or {}
    success = int(h.get('success', 0) or 0)
    failures = int(h.get('failures', 0) or 0)
    total = success + failures
    success_ratio = (success / total) if total else 0.0
    return health, age_minutes, success, failures, total, success_ratio


def reasons_for(health, age_minutes, success, total, success_ratio, is_open):
    reasons = []
    if is_open:
        if age_minutes is None:
            reasons.append('لا يوجد سجل لفحص سوق ناجح')
        elif age_minutes > MAX_STALE_MINUTES:
            reasons.append(f'آخر فحص للسوق قبل {age_minutes:.0f} دقيقة')
        if health.get('status') != 'OK':
            reasons.append(f"حالة بيانات السوق: {health.get('status', 'UNKNOWN')}")
        if total and success < MIN_EXPECTED_SUCCESS:
            reasons.append(f'عدد مصادر/طلبات البيانات الناجحة منخفض: {success}')
        if total and success_ratio < 0.35:
            reasons.append(f'نسبة نجاح بيانات السوق منخفضة: {success_ratio:.0%}')
    return reasons


def recovery_scan():
    env = os.environ.copy()
    env['GITHUB_EVENT_NAME'] = 'workflow_dispatch'
    env['FORCE_RUN'] = '1'
    env['DIAGNOSTICS_NOTIFY_ON_PUSH'] = 'true'
    print('Watchdog: stale scan detected; starting self-healing recovery scan')
    engine = subprocess.run([sys.executable, 'run.py'], cwd=ROOT, env=env, check=False)
    if engine.returncode != 0:
        print(f'WATCHDOG_RECOVERY_ENGINE_FAILED returncode={engine.returncode}')
        return False
    diagnostics = subprocess.run([sys.executable, 'diagnostics.py'], cwd=ROOT, env=env, check=False)
    if diagnostics.returncode != 0:
        print(f'WATCHDOG_RECOVERY_DIAGNOSTICS_FAILED returncode={diagnostics.returncode}')
        return False
    print('WATCHDOG_RECOVERY_SCAN_SUCCESS')
    return True


def main():
    state_path = ROOT / 'data/watchdog_state.json'
    state = load_json(state_path, {})
    now = datetime.now(timezone.utc)
    is_open = market_open()

    health, age_minutes, success, failures, total, success_ratio = health_snapshot(now)
    reasons = reasons_for(health, age_minutes, success, total, success_ratio, is_open)
    unhealthy = bool(reasons)
    recovered_by_self_heal = False

    if unhealthy and is_open and SELF_HEAL:
        recovered_by_self_heal = recovery_scan()
        if recovered_by_self_heal:
            now = datetime.now(timezone.utc)
            health, age_minutes, success, failures, total, success_ratio = health_snapshot(now)
            reasons = reasons_for(health, age_minutes, success, total, success_ratio, is_open)
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
            'تمت محاولة Recovery Scan تلقائيًا، لكن المشكلة ما زالت قائمة.' if SELF_HEAL else 'لن أعتبر عدم وجود فرص نتيجة موثوقة حتى تعود الفحوصات للعمل.'
        ]
        send('\n'.join(msg))
        state['last_alert_at'] = now.isoformat()

    elif not unhealthy and (previous_unhealthy or recovered_by_self_heal) and is_open:
        if telegram_enabled() and not recovered_by_self_heal:
            age_text = f'{age_minutes:.0f} دقيقة' if age_minutes is not None else 'غير معروف'
            send(
                '✅ <b>Trading Bot Watchdog — Recovery</b>\n'
                f'المراقبة عادت للعمل بشكل طبيعي.\n'
                f'آخر فحص: قبل {age_text}\n'
                f'بيانات السوق الناجحة: {success} | الفاشلة: {failures}'
            )
        state['last_recovery_at'] = now.isoformat()
        if recovered_by_self_heal:
            state['last_self_heal_at'] = now.isoformat()

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
        'self_heal_enabled': SELF_HEAL,
        'self_heal_triggered': recovered_by_self_heal,
    })
    save_json(state_path, state)

    print(
        f"Watchdog: market_open={is_open} unhealthy={unhealthy} "
        f"last_scan_age={state['last_scan_age_minutes']}m success={success} failures={failures} "
        f"self_heal={recovered_by_self_heal}"
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
