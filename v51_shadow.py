"""Forward-only shadow runner for V5.1 challenger. Research/paper only; never live."""
from __future__ import annotations
import os
import v49_shadow as base
import v51_active_challenger as v51

base.ENGINE = "V5.1-Active-Challenger"
base.MODE = "FORWARD_ONLY_SHADOW_RESEARCH"
base.THRESHOLD = 60
base.STATE_PATH = base.ROOT / "data/v51_forward_shadow.json"
base.SNAPSHOT_PATH = base.ROOT / "data/v51_shadow_snapshot.json"

_original_fingerprint = base.strategy_fingerprint

def strategy_fingerprint():
    # Include challenger definition so any logic change starts a new forward generation.
    import hashlib
    h = hashlib.sha256()
    for path in [
        base.ROOT / "backtest_portfolio_momentum.py",
        base.ROOT / "backtest_adaptive_momentum.py",
        base.ROOT / "backtest_evidence_momentum.py",
        base.ROOT / "v49_shadow.py",
        base.ROOT / "v51_active_challenger.py",
        base.ROOT / "v51_shadow.py",
        base.ROOT / "config/groups.yml",
        base.ROOT / "config/universe.yml",
        base.SHARIA_CONFIG,
    ]:
        h.update(str(path.relative_to(base.ROOT)).encode()); h.update(path.read_bytes())
    h.update(f"threshold={base.THRESHOLD}|cost={base.COST_BPS_PER_SIDE}".encode())
    return h.hexdigest()[:20]
base.strategy_fingerprint = strategy_fingerprint
base.generation_id = lambda fp: f"v51-{fp[:12]}"

# Reuse battle-tested forward execution/state/risk mechanics, but replace only
# the signal qualification/ranking definition with the independently validated V5.1 hypothesis.
_original_enrich = base.am._enrich_leadership
def _v51_enrich(rows, prices):
    return v51.qualify(_original_enrich(rows, prices))
base.am._enrich_leadership = _v51_enrich

_original_message = base._message
def _message(kind, x):
    msg = _original_message(kind, x)
    return msg.replace("V4.9", "V5.1 CHALLENGER").replace("V49", "V51")
base._message = _message

if __name__ == "__main__":
    # Existing runner uses V49_SHADOW_BOOTSTRAP internally; map a dedicated flag.
    if os.getenv("V51_SHADOW_BOOTSTRAP", "0").lower() in ("1","true","yes"):
        os.environ["V49_SHADOW_BOOTSTRAP"] = "1"
    raise SystemExit(base.main())
