"""V8 controlled adaptive learner."""
from __future__ import annotations
import hashlib, json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

EPOCH="V7.2_FORWARD_2026-09-09"
STATE_PATH=Path("data/v7_paper_state.json")
SNAPSHOT_PATH=Path("data/v7_decision_snapshot.json")
OUT_PATH=Path("data/v8_adaptive_learner.json")
PRIOR_STRENGTH=8.0
MIN_SEGMENT_SAMPLES=6
MIN_FORWARD_SAMPLES=30
MAX_SCORE_ADJUSTMENT=6.0
IMMUTABLE_GUARDRAILS={
 "live_execution_authorized":False,"may_modify_v7":False,
 "may_modify_risk_limits":False,"may_override_sharia_screen":False,
 "risk_per_trade_pct":0.5,"max_open_risk_pct":1.5,
 "max_group_risk_pct":1.0,"max_pair_correlation":0.85,
}

def _f(v,d=0.0):
    try:return float(v)
    except (TypeError,ValueError):return d

def _load(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except Exception:return default

def _clean_closed(state):
    return [x for x in state.get("closed",[]) if x.get("paper_epoch")==EPOCH and x.get("r") is not None]

def _trade_stats(trades):
    rs=[_f(x.get("r")) for x in trades]
    if not rs:return {"samples":0,"win_rate":0.0,"expectancy_r":0.0,"profit_factor":0.0,"total_r":0.0}
    wins=[r for r in rs if r>0]; losses=[-r for r in rs if r<0]
    pf=sum(wins)/sum(losses) if losses else (99.0 if wins else 0.0)
    return {"samples":len(rs),"win_rate":round(100*len(wins)/len(rs),1),
            "expectancy_r":round(sum(rs)/len(rs),3),"profit_factor":round(pf,2),
            "total_r":round(sum(rs),3)}

def _segment_model(trades,field,prior_mean=0.0):
    groups=defaultdict(list)
    for t in trades:groups[str(t.get(field) or "UNKNOWN")].append(t)
    out={}
    for key,rows in sorted(groups.items()):
        s=_trade_stats(rows); n=s["samples"]
        post=(s["total_r"]+PRIOR_STRENGTH*prior_mean)/(n+PRIOR_STRENGTH)
        rel=min(1.0,n/max(float(MIN_SEGMENT_SAMPLES),1.0))
        adj=max(-MAX_SCORE_ADJUSTMENT,min(MAX_SCORE_ADJUSTMENT,post*4.0*rel))
        out[key]={**s,"posterior_expectancy_r":round(post,3),"reliability":round(rel,3),
                  "suggested_score_adjustment":round(adj,2) if n>=MIN_SEGMENT_SAMPLES else 0.0,
                  "recommendation_status":"ACTIONABLE_SHADOW" if n>=MIN_SEGMENT_SAMPLES else "OBSERVE_ONLY"}
    return out

def _matrix_model(trades,prior_mean=0.0):
    groups=defaultdict(list)
    for t in trades:
        groups[f"{t.get('setup') or 'UNKNOWN'}|{t.get('signal_regime') or 'UNKNOWN'}"].append(t)
    out={}
    for key,rows in sorted(groups.items()):
        s=_trade_stats(rows); n=s["samples"]
        post=(s["total_r"]+PRIOR_STRENGTH*prior_mean)/(n+PRIOR_STRENGTH)
        rel=min(1.0,n/max(float(MIN_SEGMENT_SAMPLES),1.0))
        adj=max(-MAX_SCORE_ADJUSTMENT,min(MAX_SCORE_ADJUSTMENT,post*4.0*rel))
        out[key]={**s,"posterior_expectancy_r":round(post,3),
                  "suggested_score_adjustment":round(adj,2) if n>=MIN_SEGMENT_SAMPLES else 0.0,
                  "recommendation_status":"ACTIONABLE_SHADOW" if n>=MIN_SEGMENT_SAMPLES else "OBSERVE_ONLY"}
    return out

def _signature(clean):
    stable=[{"symbol":x.get("symbol"),"setup":x.get("setup"),"regime":x.get("signal_regime"),
             "closed_at":x.get("closed_at"),"r":_f(x.get("r"))} for x in clean]
    raw=json.dumps(stable,sort_keys=True,separators=(",",":"))
    return hashlib.sha256(raw.encode()).hexdigest()

def _candidate_shadow_rankings(snapshot,setup_model,regime_model,matrix_model):
    regime=str(snapshot.get("market_regime") or "UNKNOWN")
    ranked=[]
    for p in snapshot.get("paper_picks") or []:
        setup=str(p.get("setup") or "UNKNOWN"); base=_f(p.get("score"))
        a=_f((setup_model.get(setup) or {}).get("suggested_score_adjustment"))
        b=_f((regime_model.get(regime) or {}).get("suggested_score_adjustment"))
        c=_f((matrix_model.get(f"{setup}|{regime}") or {}).get("suggested_score_adjustment"))
        adj=max(-MAX_SCORE_ADJUSTMENT,min(MAX_SCORE_ADJUSTMENT,a*.45+b*.25+c*.30))
        ranked.append({"symbol":p.get("symbol"),"setup":setup,"market_regime":regime,
                       "v7_score":base,"v8_shadow_adjustment":round(adj,2),
                       "v8_shadow_score":round(base+adj,2),"v7_status":p.get("status"),
                       "sharia_status":p.get("sharia_status")})
    return sorted(ranked,key=lambda x:x["v8_shadow_score"],reverse=True)

def build_report(state,snapshot=None):
    clean=_clean_closed(state); champ=_trade_stats(clean)
    prior=champ["expectancy_r"] if champ["samples"]>=10 else 0.0
    sm=_segment_model(clean,"setup",prior); rm=_segment_model(clean,"signal_regime",prior); mm=_matrix_model(clean,prior)
    n=champ["samples"]
    status="LEARNING" if n<MIN_FORWARD_SAMPLES else ("READY_FOR_SHADOW_CHALLENGER" if champ["expectancy_r"]>0 and champ["profit_factor"]>=1.30 else "LEARNING_REVIEW_REQUIRED")
    report={"engine":"V8-Controlled-Adaptive-Learner","mode":"SHADOW_LEARNING_ONLY",
            "source_epoch":EPOCH,"source_signature":_signature(clean),
            "generated_at":datetime.now(timezone.utc).isoformat(),"status":status,
            "champion":{"name":"V7.2",**champ,"minimum_forward_samples":MIN_FORWARD_SAMPLES},
            "learner":{"method":"EMPIRICAL_BAYES_SHRINKAGE","prior_strength":PRIOR_STRENGTH,
                       "min_segment_samples":MIN_SEGMENT_SAMPLES,"max_score_adjustment":MAX_SCORE_ADJUSTMENT,
                       "setup_model":sm,"regime_model":rm,"setup_regime_model":mm},
            "guardrails":dict(IMMUTABLE_GUARDRAILS),
            "promotion":{"automatic_promotion":False,"human_review_required":True,
                         "minimum_forward_samples_met":n>=MIN_FORWARD_SAMPLES,
                         "shadow_comparison_required":True,"live_execution_authorized":False}}
    if snapshot is not None:report["shadow_rankings"]=_candidate_shadow_rankings(snapshot,sm,rm,mm)
    return report

def main():
    state=_load(STATE_PATH,{}); snapshot=_load(SNAPSHOT_PATH,{})
    report=build_report(state,snapshot); previous=_load(OUT_PATH,{})
    if previous.get("source_signature")==report["source_signature"]:
        report["generated_at"]=previous.get("generated_at",report["generated_at"])
    OUT_PATH.parent.mkdir(parents=True,exist_ok=True)
    text=json.dumps(report,indent=2,ensure_ascii=False)+"\n"
    current=OUT_PATH.read_text(encoding="utf-8") if OUT_PATH.exists() else None
    if current!=text:OUT_PATH.write_text(text,encoding="utf-8")
    print(f"V8 learner: samples={report['champion']['samples']} status={report['status']}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
