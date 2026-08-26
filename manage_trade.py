from pathlib import Path
import argparse,sys
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'src'))
from trading_bot.portfolio import PortfolioStore
def main():
    p=argparse.ArgumentParser();p.add_argument('--action',choices=['BUY','SELL'],required=True);p.add_argument('--symbol',required=True);p.add_argument('--qty',type=float,required=True);p.add_argument('--price',type=float,required=True);p.add_argument('--stop',type=float,default=0);p.add_argument('--target1',type=float,default=0);p.add_argument('--target2',type=float,default=0);p.add_argument('--strategy',default='manual');a=p.parse_args();s=PortfolioStore(ROOT/'data')
    s.buy(a.symbol.upper(),a.qty,a.price,a.stop,a.target1,a.target2,a.strategy) if a.action=='BUY' else s.sell(a.symbol.upper(),a.qty,a.price);print(s.summary());return 0
if __name__=='__main__':raise SystemExit(main())
