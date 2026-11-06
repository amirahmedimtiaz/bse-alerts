"""Official NSE adapters for Alpex. Transport and parsing fail closed."""
from __future__ import annotations
import csv
import hashlib
import io
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlencode, urlparse
from .http_client import _pace

SYMBOL = 'ALPEXSOLAR'
ISIN = 'INE0R4701017'
BASE = 'https://www.nseindia.com'
PAGES = {
 'bulk': BASE+'/report-detail/display-bulk-and-block-deals',
 'block': BASE+'/report-detail/display-bulk-and-block-deals',
 'pit': BASE+'/companies-listing/corporate-filings-insider-trading',
 'sast': BASE+'/companies-listing/corporate-filings-regulation-29',
 'pledge': BASE+'/companies-listing/corporate-filings-regulation-31',
 'shareholding': BASE+'/companies-listing/corporate-filings-shareholding-pattern',
 'capital': BASE+'/get-quote/equity/ALPEXSOLAR/Alpex-Solar-Limited',
}
PATHS = {'pit':'corporates-pit-gg','sast':'corporate-sast-reg29',
         'pledge':'corporate-pledgedata-sast3132','shareholding':'corporate-share-holdings-master'}


def read_url(url: str) -> str:
    host=urlparse(url).hostname
    if urlparse(url).scheme!='https' or host not in ('www.nseindia.com','nsearchives.nseindia.com','archives.nseindia.com'):
        raise ValueError('Unexpected source URL')
    # curl works with both local macOS and hosted Linux TLS stacks. Never execute a shell.
    for attempt in range(2):
        _pace(host)
        proc=subprocess.run(['curl','--silent','--show-error','--fail','--location',
            '--proto','=https','--proto-redir','=https','--connect-timeout','8','--max-time','20',
            '--max-filesize','5000000','--user-agent','Mozilla/5.0',
            '--referer',BASE+'/',url],capture_output=True,text=True,timeout=25)
        if proc.returncode==0:
            return proc.stdout.lstrip('\ufeff')
        if attempt==0: time.sleep(2)
    raise RuntimeError(f'NSE request failed ({proc.returncode}): {proc.stderr[:240]}')


def table(payload):
    rows=payload if isinstance(payload,list) else payload.get('data') if isinstance(payload,dict) else None
    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
        raise ValueError('NSE response missing a valid data table')
    if isinstance(payload,dict) and payload.get('error'): raise ValueError('NSE returned an error')
    return rows


def iso(value):
    value=str(value or '').strip().replace(',', '')
    for fmt in ('%Y-%m-%dT%H:%M:%S','%Y-%m-%d %H:%M:%S','%d-%b-%Y %H:%M:%S','%d-%b-%Y %H:%M','%d-%b-%Y','%d-%m-%Y','%Y-%m-%d'):
        try: return datetime.strptime(value,fmt).isoformat() if '%H' in fmt else datetime.strptime(value,fmt).date().isoformat()
        except ValueError: pass
    raise ValueError(f'Invalid NSE date: {value!r}')


def number(value):
    d=Decimal(str(value).replace(',','').strip())
    if not d.is_finite() or d<0: raise ValueError('Invalid nonnegative quantity/value')
    return format(d,'f')


def version(event):
    stable={k:v for k,v in event.items() if k not in ('source_url','detected_at','_version')}
    return hashlib.sha256(json.dumps(stable,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def parse_deals(text: str, kind: str) -> list[dict]:
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    expected={'Date','Symbol','Client Name','Buy / Sell','Quantity Traded','Trade Price / Wght. Avg. Price'}
    if not reader.fieldnames: raise ValueError('Missing CSV header')
    reader.fieldnames=[re.sub(r'\s+',' ',k.strip()) for k in reader.fieldnames]
    # Daily archive uses Buy/Sell; historical download uses Buy / Sell.
    reader.fieldnames=['Buy / Sell' if k=='Buy/Sell' else k for k in reader.fieldnames]
    if not expected.issubset(reader.fieldnames): raise ValueError('Invalid NSE deal CSV header')
    result=[]
    for row in reader:
        if row.get('Symbol','').strip()!=SYMBOL: continue
        side=row['Buy / Sell'].strip().upper()
        if side not in ('BUY','SELL'): raise ValueError('Invalid deal side')
        qty=number(row['Quantity Traded']); price=number(row['Trade Price / Wght. Avg. Price'])
        if Decimal(qty)!=Decimal(qty).to_integral_value(): raise ValueError('Non-integer share quantity')
        day=iso(row['Date']); person=row['Client Name'].strip()
        if not person: raise ValueError('Missing participant')
        result.append({'kind':kind,'symbol':SYMBOL,'event_date':day,'published_at':day,
            'source_id':f'{kind}:{day}:{person}:{side}','participant':person,'side':side,
            'quantity':qty,'price':price,'calculated_value_inr':number(Decimal(qty)*Decimal(price)),
            'details':{'remarks':row.get('Remarks','').strip()},'source_url':PAGES[kind]})
    return result


def xml_details(text: str, symbol: str=SYMBOL):
    if '<!DOCTYPE' in text.upper(): raise ValueError('Unexpected XML doctype')
    root=ET.fromstring(text)
    facts={}
    for el in root:
        tag=el.tag.split('}')[-1]; ctx=el.get('contextRef')
        if ctx and el.text and el.text.strip() and not any(t in tag.lower() for t in ('pan','identification','address','signatory','registeredowner')):
            facts.setdefault(ctx,{})[tag]=el.text.strip()
    symbols=[v.get('Symbol') for v in facts.values() if v.get('Symbol')]
    if symbols!=[symbol]: raise ValueError('XML symbol does not match issuer')
    typed={el.get('id') for el in root if el.tag.split('}')[-1]=='context'
           and any(c.tag.split('}')[-1]=='typedMember' for c in el.iter())}
    participants=[]; holdings={}; unmatched=[]
    for ctx,f in facts.items():
        if 'NameOfThePerson' in f:
            # Preserve transaction mode; an inter-se transfer is not open-market buying.
            participants.append(f)
        if 'NumberOfShares' in f:
            name=f.get('NameOfTheShareholder')
            if not name and ctx in typed:
                unmatched.append(ctx)
                continue
            label=('holder: '+' '.join(name.upper().split())) if name else ('category: '+ctx.removesuffix('_ContextI'))
            holdings[label]={k:v for k,v in f.items() if k in (
                'NumberOfShares','ShareholdingAsAPercentageOfTotalNumberOfShares',
                'NumberOfSharesEncumberedUnderPledged','NumberOfSharesUnderlyingOutstandingWarrants')}
    return {'participants':participants,'holdings':holdings,'unmatched_contexts':unmatched}


def fetch_feed(kind: str, start: date, end: date, *, read=read_url) -> list[dict]:
    params={'from_date':start.strftime('%d-%m-%Y'),'to_date':end.strftime('%d-%m-%Y'),
            'symbol':SYMBOL,'index':'sme'}
    if kind == 'capital':
        url=BASE+'/api/NextApi/apiClient/GetQuoteApi?'+urlencode({
            'functionName':'getCorporateAnnouncement','symbol':SYMBOL,'marketApiType':'sme'})
        result=[]
        for row in table(json.loads(read(url))):
            if row.get('symbol')!=SYMBOL: raise ValueError('Wrong corporate issuer')
            stamp=iso(row.get('sort_date') or row.get('dt'))
            if not start<=date.fromisoformat(stamp[:10])<=end: continue
            text=str(row.get('desc',''))+' '+str(row.get('attchmntText',''))
            if not is_capital(text): continue
            attachment=row.get('attchmntFile')
            sid=row.get('seq_id') or version({'stamp':stamp,'attachment':attachment})
            result.append({'kind':kind,'symbol':SYMBOL,'published_at':stamp,'event_date':stamp[:10],
                'source_id':'capital:'+str(sid),'source_url':url,'document_url':attachment,
                'details':{'subject':row.get('desc'),'headline':row.get('attchmntText'),
                'interpretation':'Source wording only. A proposal, approval or trading approval is not proof of completed issuance.'}})
        return result
    if kind in ('bulk','block'):
        url=BASE+'/api/historicalOR/bulk-block-short-deals?'+urlencode({
            'optionType':kind+'_deals','symbol':SYMBOL,'from':start.strftime('%d-%m-%Y'),
            'to':end.strftime('%d-%m-%Y'),'csv':'true'})
        events=parse_deals(read(url),kind)
    else:
        url=BASE+'/api/'+PATHS[kind]+'?'+urlencode(params)
        raw=table(json.loads(read(url))); events=[]
        for row in raw:
            if row.get('symbol')!=SYMBOL: raise ValueError('NSE ignored issuer filter')
            stamp=next((row[k] for k in ('broadcastDateTime','broadcastDate','broadcastdate','timestamp','sysTime') if row.get(k)),None)
            published=iso(stamp)
            sid=next((str(row[k]) for k in ('appId','application_no','seqId','recordId') if row.get(k)),None)
            if not sid: raise ValueError('Missing source ID')
            details={k:v for k,v in row.items() if v is not None and not any(x in k.lower() for x in ('pan','filesize','timedifference','diff'))}
            attachment=next((row[k] for k in ('xmlFileName','xbrl','attachment','attachement') if row.get(k)),None)
            if kind in ('pit','shareholding'):
                if not attachment: raise ValueError('Missing XBRL source')
                details.update(xml_details(read(attachment)))
                if kind=='shareholding' and not details['holdings']: raise ValueError('Unsupported shareholding XML schema')
                if kind=='pit' and not details['participants']: raise ValueError('Unsupported PIT XML schema')
            events.append({'kind':kind,'symbol':SYMBOL,'published_at':published,
                'event_date':row.get('date') or row.get('eventDate') or row.get('acquirerDate'),
                'source_id':kind+':'+sid,'source_url':url,'document_url':attachment,'details':details})
    for e in events:
        if not start<=date.fromisoformat(e['published_at'][:10])<=end:
            raise ValueError('NSE response outside requested date range')
        e['source_url']=url
    return events


def is_capital(text):
    return bool(re.search(r'allot|warrant|preferential|rights issue|bonus|split|sub.?division|convertib|conversion|share capital|fund.?rais|further issue',text,re.I))
