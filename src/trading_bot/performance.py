from __future__ import annotations
import json
from pathlib import Path

def summarize(trades):
    realized=[float(x.get('realized_pnl',0)) for x in trades if x.get('action')=='SELL' and 'realized_pnl' in x]
    wins=[x for x in realized if x>0];losses=[x for x in realized if x<0];gp=sum(wins);gl=abs(sum(losses))
    return {'closed_trades':len(realized),'wins':len(wins),'losses':len(losses),'win_rate':round(len(wins)/len(realized)*100,1) if realized else 0.0,'realized_pnl':round(sum(realized),2),'profit_factor':round(gp/gl,2) if gl else (999.0 if gp else 0.0),'avg_win':round(sum(wins)/len(wins),2) if wins else 0.0,'avg_loss':round(sum(losses)/len(losses),2) if losses else 0.0}
def save(data_dir):
    d=Path(data_dir)
    try:trades=json.loads((d/'trades.json').read_text(encoding='utf-8'))
    except Exception:trades=[]
    out=summarize(trades);(d/'performance.json').write_text(json.dumps(out,indent=2),encoding='utf-8');return out
