from __future__ import annotations
import json
from pathlib import Path
import yfinance as yf
import pandas as pd
import yaml
from .strategies import swing_setup
ROOT=Path(__file__).resolve().parents[2]
def run_backtest():
    cfg=yaml.safe_load((ROOT/'config/settings.yml').read_text())['settings'];universe=yaml.safe_load((ROOT/'config/universe.yml').read_text())['universe'][:30];trades=[]
    for symbol in universe:
        try:
            df=yf.download(symbol,period='2y',interval='1d',auto_adjust=False,progress=False,threads=False)
            if isinstance(df.columns,pd.MultiIndex):
                if symbol in df.columns.get_level_values(-1):df=df.xs(symbol,axis=1,level=-1)
                else:df.columns=df.columns.get_level_values(0)
            df=df.dropna(subset=['Open','High','Low','Close'])
            for i in range(60,len(df)-6,5):
                s=swing_setup(symbol,df.iloc[:i+1])
                if not s or s.raw_score<int(cfg.get('backtest_min_score',82)):continue
                future=df.iloc[i+1:i+6];outcome='TIME';exit_price=float(future['Close'].iloc[-1])
                for _,r in future.iterrows():
                    if float(r['Low'])<=s.stop_loss:outcome='LOSS';exit_price=s.stop_loss;break
                    if float(r['High'])>=s.target1:outcome='WIN';exit_price=s.target1;break
                trades.append({'symbol':symbol,'score':s.raw_score,'outcome':outcome,'return_pct':round((exit_price-s.price)/s.price*100,2)})
        except Exception as exc:print('BT',symbol,exc)
    wins=[x for x in trades if x['outcome']=='WIN'];losses=[x for x in trades if x['outcome']=='LOSS'];avg=sum(x['return_pct'] for x in trades)/len(trades) if trades else 0
    out={'samples':len(trades),'wins':len(wins),'losses':len(losses),'win_rate':round(len(wins)/max(len(wins)+len(losses),1)*100,1),'avg_return_pct':round(avg,2),'note':'Simplified daily swing backtest; not transaction-cost adjusted.'};(ROOT/'data/backtest.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(out);return 0
