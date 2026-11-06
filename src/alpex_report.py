"""Export the persisted monitor, or perform a read-only live source check (no email)."""
from __future__ import annotations
import argparse
import base64
import csv
import html
import json
import subprocess
from pathlib import Path
from .alert_store import AlertStore
from .alpex_monitor import state, collect, describe, LIMITS
from .alpex_sources import PAGES


def export(data, directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    stem=directory/'Alpex_Solar_Trade_Ownership_Monitor'
    stem.with_suffix('.json').write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
    fields=['kind','source_id','published_at','event_date','participant','side','quantity','price','calculated_value_inr','source_url','document_url','detected_at','details']
    with stem.with_suffix('.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fields,extrasaction='ignore');writer.writeheader()
        for event in data['events']:
            writer.writerow({**event,'details':json.dumps(event.get('details',{}),ensure_ascii=False)})
    esc=html.escape
    health=''.join('<tr><td>'+esc(kind)+'</td><td>'+esc(str(feed.get('last_success','never')))+'</td><td>'+esc(feed.get('error') or 'Successful response')+'</td></tr>' for kind,feed in data['feeds'].items())
    records=''.join('<details><summary>'+esc(e['kind']+' | '+e['published_at']+' | '+e.get('participant',e['source_id']))+'</summary><pre>'+esc(describe(e,data['events']))+'</pre></details>' for e in sorted(data['events'],key=lambda e:e['published_at'],reverse=True))
    links=''.join('<li><a href="'+esc(url,quote=True)+'">'+esc(kind)+'</a></li>' for kind,url in PAGES.items())
    content='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Alpex Solar trade and ownership monitor</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;background:#f6f8fa;color:#142334}table{border-collapse:collapse;width:100%;background:white}td,th{padding:12px;border:1px solid #ccc;text-align:left}details{margin:12px 0;padding:16px;background:white;border-radius:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px system-ui}a{color:#165aad}</style><h1>Alpex Solar: trade and ownership monitor</h1>'''
    content+='<p>Official NSE sources • ALPEXSOLAR • INE0R4701017</p><p>Activated: '+esc(data.get('activated_at','not activated'))+'</p>'
    content+='<p>'+esc(LIMITS)+'</p><p>Reported facts retain source precision. Approximate deal values and percentage-point changes are calculations. Raw XBRL shareholding percentages are fractions. History retains revisions; absent rows are not automatically labelled cancellations. Categories and individual holders overlap and must not be summed.</p>'
    content+='<h2>Coverage</h2><table><tr><th>Feed</th><th>Last complete date</th><th>Status</th></tr>'+health+'</table><p>A successful empty result means no matching rows in the requested published-date window; it does not prove absence of trading. The quote API capital feed has unproven historical completeness. Latest-only daily files are not used as historical substitutes.</p>'
    content+='<h2>Track disclosures yourself</h2><ul>'+links+'</ul><p>Select SME where offered and search ALPEXSOLAR. On the deal archive, choose Bulk or Block, choose dates and download the CSV: the on-page table is limited. Shareholding filings are periodic snapshots, not live holdings.</p><h2>History and evidence</h2>'+records+'</html>'
    stem.with_suffix('.html').write_text(content)
    return stem.with_suffix('.html')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remote',action='store_true',help='Read production GitHub state instead of polling NSE')
    parser.add_argument('--output-dir',default='reports')
    args=parser.parse_args()
    store=AlertStore(':memory:')
    try:
        if args.remote:
            result=subprocess.run(['gh','api','repos/amirahmedimtiaz/bse-alerts/contents/alerts.json?ref=alert-state'],capture_output=True,text=True,check=True,timeout=45)
            payload=json.loads(result.stdout)
            store.restore(json.loads(base64.b64decode(payload['content'])))
        else:
            collect(store)
        data=state(store)
        path=export(data,args.output_dir)
        print(json.dumps({'report':str(path),'events':len(data['events']),'feeds':data['feeds']},indent=2))
    finally:store.close()

if __name__=='__main__':main()
