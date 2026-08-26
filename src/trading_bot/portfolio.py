from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
class PortfolioStore:
    def __init__(self,data_dir):
        self.data_dir=Path(data_dir);self.portfolio_path=self.data_dir/'portfolio.json';self.trades_path=self.data_dir/'trades.json';self.data_dir.mkdir(parents=True,exist_ok=True)
    def _load(self,p,d):
        try:return json.loads(p.read_text(encoding='utf-8')) if p.exists() else d
        except Exception:return d
    def _save(self,p,d):p.write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding='utf-8')
    def portfolio(self):return self._load(self.portfolio_path,{'cash':10000.0,'starting_equity':10000.0,'positions':[]})
    def trades(self):return self._load(self.trades_path,[])
    def buy(self,symbol,qty,price,stop=0,target1=0,target2=0,strategy='manual'):
        p=self.portfolio(); cost=qty*price
        if cost>p.get('cash',0):raise ValueError('Insufficient cash')
        if any(x['symbol']==symbol for x in p['positions']):raise ValueError('Position already exists')
        p['cash']=round(p['cash']-cost,2);p['positions'].append({'symbol':symbol,'qty':qty,'avg_price':price,'stop':stop,'target1':target1,'target2':target2,'strategy':strategy,'opened_at':datetime.now(timezone.utc).isoformat()});p['updated_at']=datetime.now(timezone.utc).isoformat();self._save(self.portfolio_path,p)
        t=self.trades();t.append({'action':'BUY','symbol':symbol,'qty':qty,'price':price,'time':p['updated_at'],'strategy':strategy});self._save(self.trades_path,t)
    def sell(self,symbol,qty,price):
        p=self.portfolio();pos=next((x for x in p['positions'] if x['symbol']==symbol),None)
        if not pos:raise ValueError('Position not found')
        if qty>pos['qty']:raise ValueError('Quantity exceeds position')
        realized=(price-pos['avg_price'])*qty;p['cash']=round(p['cash']+price*qty,2);pos['qty']-=qty
        if pos['qty']<=0:p['positions'].remove(pos)
        p['updated_at']=datetime.now(timezone.utc).isoformat();self._save(self.portfolio_path,p)
        t=self.trades();t.append({'action':'SELL','symbol':symbol,'qty':qty,'price':price,'realized_pnl':round(realized,2),'time':p['updated_at']});self._save(self.trades_path,t)
    def summary(self):
        p=self.portfolio();return {'cash':p.get('cash',0),'positions':len(p.get('positions',[]))}
