from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml
from .telegram import enabled as telegram_enabled, send

ROOT = Path(__file__).resolve().parents[2]
GRADE = {'A+': 4, 'A': 3, 'B': 2, 'C': 1}
ACTION = {'BUY_NOW': 5, 'WAIT_FOR_ENTRY': 4, 'DO_NOT_CHASE': 3, 'WATCH': 2, 'NO_TRADE': 0}

def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default

def _save_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

def _load_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8'))

def _group_for(symbol, groups):
    for name, symbols in (groups or {}).items():
        if symbol in symbols:
            return name
    return 'OTHER'

def _rank(x):
    return (
        GRADE.get(x.get('quality_grade', 'C'), 0),
        ACTION.get(x.get('action', ''), 0),
        int(x.get('score', 0)),
        bool(x.get('confirmation_passed', False)),
        float(x.get('precision', {}).get('relative_strength', {}).get('vs_market', 0)),
    )

def _eligible(x):
    return (
        x.get('signal') in ('BUY', 'STRONG_BUY')
        and x.get('quality_grade') in ('A+', 'A', 'B')
        and x.get('action') in ('BUY_NOW', 'WAIT_FOR_ENTRY')
    )

def _reasons(x, settings):
    if _eligible(x):
        return ['مؤهلة وفق شروط الاستراتيجية الحالية']
    reasons = []
    score = int(x.get('score', 0))
    buy_score = int(settings.get('buy_score', 85))
    min_rr = float(settings.get('min_risk_reward', 2.0))
    try:
        rr1 = float(x.get('rr1', 0))
    except Exception:
        rr1 = 0.0

    if x.get('validation_blocked'):
        reasons.append('التحقق التاريخي أوقف هذا النوع مؤقتًا')
    if x.get('daily_loss_blocked'):
        reasons.append('حماية الصفقات الجديدة مفعلة حاليًا')
    if x.get('event_risk') == 'HIGH':
        reasons.append('مخاطر حدث/أرباح مرتفعة')
    if rr1 < min_rr:
        reasons.append(f'العائد/المخاطرة {rr1:.2f} أقل من الحد {min_rr:.2f}')
    if score < buy_score:
        reasons.append(f'الدرجة {score} أقل من حد BUY {buy_score}')
    if score >= buy_score and not x.get('confirmation_passed', True):
        reasons.append(f"تأكيدات الدخول غير كافية ({x.get('confirmation_reason', 'غير مكتملة')})")
    if x.get('quality_grade') == 'C':
        reasons.append('درجة جودة الفرصة C لا تسمح بالدخول')

    action = x.get('action')
    if action == 'DO_NOT_CHASE':
        reasons.append('السعر أعلى من منطقة الدخول المناسبة')
    elif action == 'WATCH' and score >= buy_score and rr1 >= min_rr:
        reasons.append('التوقيت أو التأكيدات لم تكتمل بعد')
    elif action == 'NO_TRADE' and not reasons:
        reasons.append(x.get('instruction') or 'لا توجد صفقة مناسبة الآن')

    for blocker in x.get('precision', {}).get('blockers', []):
        if blocker and blocker not in reasons:
            reasons.append(blocker)

    if x.get('signal') == 'BLOCKED':
        for key in ('portfolio_gate', 'correlation_gate'):
            value = x.get(key)
            if value and str(value).upper() != 'OK' and value not in reasons:
                reasons.append(str(value))

    if not reasons:
        reasons.append(x.get('instruction') or 'لم تستوفِ شروط الدخول الصارمة')
    return reasons[:4]

def build(signals, groups, settings, max_items=5):
    rows = []
    seen = set()
    for x in sorted(signals, key=_rank, reverse=True):
        symbol = x.get('symbol')
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        rows.append({
            'rank': len(rows) + 1,
            'symbol': symbol,
            'strategy': x.get('strategy'),
            'setup_type': x.get('setup_type'),
            'score': int(x.get('score', 0)),
            'quality_grade': x.get('quality_grade', 'C'),
            'signal': x.get('signal'),
            'action': x.get('action'),
            'price': x.get('price'),
            'entry_low': x.get('entry_low'),
            'entry_high': x.get('entry_high'),
            'rr1': x.get('rr1'),
            'opportunity_group': _group_for(symbol, groups),
            'eligible': _eligible(x),
            'exclusion_reasons': _reasons(x, settings),
        })
        if len(rows) >= max_items:
            break
    return rows

def _market_open():
    now = datetime.now(ZoneInfo('America/New_York'))
    minutes = now.hour * 60 + now.minute
    return now.weekday() < 5 and 570 <= minutes <= 960, now.date().isoformat()

def _message(rows, engine):
    lines = [
        f'🔎 <b>Market Discovery Diagnostics — {engine}</b>',
        'أفضل 5 فرص حسب جودة الاكتشاف، حتى لو لم تتأهل للدخول:',
    ]
    for row in rows:
        status = '✅ مؤهلة' if row['eligible'] else '❌ مستبعدة'
        reason = '؛ '.join(row['exclusion_reasons'][:3])
        lines.append(
            f"{row['rank']}) <b>{row['symbol']}</b> — "
            f"{row['score']}/{row['quality_grade']} — {status}"
        )
        lines.append(f'   السبب: {reason}')
    lines.append('ℹ️ التشخيص لا يغيّر قواعد المخاطرة أو شروط الدخول.')
    return '\n'.join(lines)

def run():
    signals = _load_json(ROOT / 'data/signals.json', [])
    settings = (_load_yaml(ROOT / 'config/settings.yml') or {}).get('settings', {})
    groups = (_load_yaml(ROOT / 'config/groups.yml') or {}).get('groups', {})
    rows = build(signals, groups, settings, 5)
    engine = signals[0].get('engine', 'Market-Discovery') if signals else 'Market-Discovery'
    payload = {
        'timestamp': datetime.now(ZoneInfo('UTC')).isoformat(),
        'engine': engine,
        'count': len(rows),
        'opportunities': rows,
    }
    _save_json(ROOT / 'data/market_discovery_diagnostics.json', payload)

    print(f'Market Discovery Diagnostics: {len(rows)} rows')
    for row in rows:
        print(
            f"#{row['rank']} {row['symbol']} score={row['score']} "
            f"grade={row['quality_grade']} eligible={row['eligible']} "
            f"reasons={' | '.join(row['exclusion_reasons'])}"
        )

    event_name = os.getenv('GITHUB_EVENT_NAME', 'local')
    notify_on_push = os.getenv('DIAGNOSTICS_NOTIFY_ON_PUSH', '').lower() in ('1', 'true', 'yes')
    market_is_open, market_day = _market_open()
    state_path = ROOT / 'data/diagnostics_notify_state.json'
    state = _load_json(state_path, {})

    should_notify = event_name == 'workflow_dispatch'
    if event_name == 'schedule' and market_is_open and state.get('market_day') != market_day:
        should_notify = True
    if event_name == 'push' and notify_on_push:
        should_notify = True

    if should_notify:
        if not telegram_enabled():
            print('MARKET_DISCOVERY_TELEGRAM_CONFIG_ERROR')
            return 3
        try:
            send(_message(rows, engine))
        except Exception as exc:
            print('MARKET_DISCOVERY_TELEGRAM_SEND_ERROR:', exc)
            return 3
        if event_name in ('schedule', 'push'):
            state['market_day'] = market_day
            state['sent_at'] = datetime.now(ZoneInfo('UTC')).isoformat()
            _save_json(state_path, state)
        print('Market Discovery Diagnostics Telegram: sent')

    return 0

if __name__ == '__main__':
    raise SystemExit(run())
