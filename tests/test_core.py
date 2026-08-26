import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import pandas as pd
from trading_bot.strategies import intraday_setup
from trading_bot.risk import size_position

def test_position_size():
    x=size_position(100,95,10000,.5,15,10000)
    assert x['shares']==10
    assert x['risk_dollars']==50

def test_intraday_setup_runs():
    n=80;idx=pd.date_range('2026-01-01',periods=n,freq='15min',tz='UTC');close=pd.Series([100+i*.1 for i in range(n)],index=idx)
    df=pd.DataFrame({'Open':close-.05,'High':close+.2,'Low':close-.2,'Close':close,'Volume':[1000000+i*1000 for i in range(n)]},index=idx)
    s=intraday_setup('TEST',df);assert s is not None;assert s.price>0
