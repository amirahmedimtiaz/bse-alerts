"""Alpex ownership collection using the existing durable ledger and outbox."""
from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from zoneinfo import ZoneInfo
from . import alpex_sources as sources

KEY='ALPEX:ownership'
META='alpex-monitor-v1'
FEEDS=('bulk','block','pit','sast','pledge','shareholding','capital')
LIMITS=('Public disclosures are not a complete investor trade/holdings ledger. '
        'Bulk/block reports appear after market hours; other disclosures may be filed after the transaction. '
        'Do not add overlapping disclosures together or infer matched counterparties. '
        'Snapshot changes may reflect capital changes, transfers or reclassification rather than market trades.')


def state(store):
    row=store.db.execute('SELECT value FROM metadata WHERE key=?',(META,)).fetchone()
    return json.loads(row[0]) if row else {'feeds':{},'events':[], 'initialized':False}


def save(store,data):
    with store.db:
        store.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(META,json.dumps(data,sort_keys=True)))


def message(identity,subject,body,now):
    return {'_id':identity,'_alpex':True,'_exchange':'NSE','_company_name':'Alpex Solar Limited',
            '_subject':'[ALPEX] '+subject,'_body':body+'\n\n'+LIMITS,
            '_published':now.isoformat(),'_page_url':sources.PAGES['bulk']}


def times(stamp):
    if len(stamp)==10: return stamp+' (date only; publication time not disclosed in this report)'
    dt=datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo('Asia/Kolkata'))
    return dt.strftime('%Y-%m-%d %H:%M:%S IST')+' / '+dt.astimezone(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S JST')


def holding_changes(new,old):
    if not old: return 'Comparison unavailable: no supported prior snapshot. REVIEW source document.'
    a=old['details'].get('holdings',{}); b=new['details'].get('holdings',{}); lines=[]
    for label in sorted(a.keys() & b.keys()):
        before=a[label];after=b[label]
        if before==after:continue
        text=f'{label}: '+before.get('NumberOfShares','?')+' → '+after.get('NumberOfShares','?')+' shares'
        field='ShareholdingAsAPercentageOfTotalNumberOfShares'
        if field in before and field in after:
            # NSE shareholding XBRL stores percentage facts as fractions of one.
            delta=(Decimal(after[field])-Decimal(before[field]))*100
            text+=f'; calculated change {delta:+.2f} pp'
        for field in ('NumberOfSharesEncumberedUnderPledged','NumberOfSharesUnderlyingOutstandingWarrants'):
            if before.get(field)!=after.get(field):text+=f'; {field}: {before.get(field,"?")} → {after.get(field,"?")}'
        lines.append(text)
    absent=(a.keys() ^ b.keys())
    if absent: lines.append('Newly disclosed or no longer disclosed (not proof of entry/exit): '+', '.join(sorted(absent)))
    return (f'Compared {old.get("event_date")} with {new.get("event_date")}.\n'+
            ('\n'.join(lines) if lines else 'No changes in comparable supported facts.')+
            '\nSnapshot differences are not proof of a trade; check share capital and classification changes.')


def describe(event,history=()):
    lines=[event['kind'].upper(),f'Transaction / period date: {event.get("event_date") or "See disclosure"}',
           ('Report trade date (publication timestamp unavailable): ' if event['kind'] in ('bulk','block') else 'Published: ')+times(event['published_at'])]
    if event['kind'] in ('bulk','block'):
        lines.extend([f"{event['participant']} | {event['side']} | {event['quantity']} shares | INR {event['price']}",
          'Calculated approximate value (quantity × reported weighted-average price): INR '+event['calculated_value_inr']])
    else:
        details=event['details']
        for k,v in details.items():
            if k not in ('holdings','participants','unmatched_contexts') and v not in ('',None):lines.append(f'{k}: {v}')
        for p in details.get('participants',[]):
            lines.append('Participant disclosure:\n'+'\n'.join(f'{k}: {v}' for k,v in p.items()))
        if event['kind']=='shareholding':
            prior=[e for e in history if e['kind']=='shareholding' and sources.iso(e['event_date'])<sources.iso(event['event_date'])]
            old=max(reversed(prior),key=lambda e:(sources.iso(e['event_date']),e['published_at']),default=None)
            lines.append(holding_changes(event,old))
            if details.get('unmatched_contexts'):
                lines.append('REVIEW: anonymous typed XBRL rows are excluded from holder/category comparisons; use the source document.')
            lines.append('XBRL percentage facts are raw fractions; overview promoter/public fields are percentages.')
    lines.append('Official data: '+event['source_url'])
    if event.get('document_url'):lines.append('Official document: '+event['document_url'])
    return '\n'.join(lines)


def collect(store,*,now=None,fetch=sources.fetch_feed):
    now=now or datetime.now(timezone.utc)
    today=now.astimezone(ZoneInfo('Asia/Kolkata')).date(); cutoff=today-timedelta(days=29)
    data=state(store); initial=not data['initialized']; errors=[]; updates=[]
    if store.company(KEY) is None:store.collect(KEY,today.isoformat(),[])
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={}
        for kind in FEEDS:
            health=data['feeds'].get(kind,{})
            last=date.fromisoformat(health['last_success'][:10]) if health.get('last_success') else cutoff
            daily=health.get('reconciled_on')!=today.isoformat()
            start=min(cutoff,last-timedelta(days=1)) if daily or initial else min(today-timedelta(days=1),last-timedelta(days=1))
            if initial and kind=='shareholding': start=today-timedelta(days=365)
            # Keep exchange windows below one year; older outages advance in chunks.
            end=min(today,start+timedelta(days=365))
            futures[pool.submit(fetch,kind,start,end)]=(kind,end,daily)
        for future in as_completed(futures):
            kind,end,daily=futures[future]; health=data['feeds'].setdefault(kind,{})
            health['last_attempt']=now.isoformat()
            try:
                events=future.result()
                history=list(data['events']); source_updates=[]
                for event in events:
                    v=sources.version(event)
                    previous=[e for e in history if e['source_id']==event['source_id']]
                    if previous and previous[-1]['_version']==v:continue
                    stored={**event,'_version':v,'detected_at':now.isoformat()}
                    history.append(stored)
                    if not initial and (kind != "capital" or previous):
                        subject=('[Correction] ' if previous else '['+kind.capitalize()+'] ')+event.get('participant',event['source_id'])
                        source_updates.append(message(v+":"+str(len(previous)+1),subject,describe(event,history),now))
                data['events']=history
                updates.extend(source_updates)
                if health.get('warned_at'):
                    updates.append(message(kind+':recovery:'+now.isoformat(),'[Coverage restored] '+kind,
                        f'{kind} recovered; successful window through {end}. Late items retained.',now))
                health.update(last_success=end.isoformat(),last_ok_at=now.isoformat(),error=None,
                              failing_since=None,warned_at=None)
                if daily and end==today:health['reconciled_on']=today.isoformat()
            except Exception as exc:
                health['error']=f'{type(exc).__name__}: {str(exc)[:260]}'
                health.setdefault('failing_since',None)
                health['failing_since']=health['failing_since'] or now.isoformat()
                errors.append(kind)
                first=datetime.fromisoformat(health['failing_since'])
                warned=datetime.fromisoformat(health['warned_at']) if health.get('warned_at') else None
                if now-first>=timedelta(minutes=30) and (not warned or now-warned>=timedelta(days=1)):
                    updates.append(message(kind+':warning:'+now.isoformat(),'[Coverage warning] '+kind,
                        f'{kind} unavailable since {first.isoformat()}. Last complete date: {health.get("last_success","never")}.\n'+health['error'],now))
                    health['warned_at']=now.isoformat()
    if initial:
        recent=[e for e in data['events'] if e['published_at'][:10]>=cutoff.isoformat()]
        lines=[f'Initial 30-day disclosure summary: {cutoff} to {today} (IST calendar dates).',
               'Detected: '+now.astimezone(ZoneInfo('Asia/Tokyo')).isoformat(),
               'Coverage:\n'+'\n'.join(f'{k}: '+('UNAVAILABLE: '+data['feeds'][k]['error'] if data['feeds'][k].get('error') else 'validated response through '+data['feeds'][k]['last_success']) for k in FEEDS),
               'Capital/dilution filings continue through the existing corporate-announcement alerts; proposals are not completed issues.']
        lines.extend(describe(e,data['events']) for e in sorted(recent,key=lambda e:(e['published_at'],e['source_id'])))
        if not recent:lines.append('No matching rows found in successfully collected sources; unavailable feeds remain unknown.')
        updates.insert(0,message('initial-30-day-summary','[30-day summary]', '\n\n'.join(lines),now))
        data['initialized']=True
        data['activated_at']=now.isoformat()
    # Durable snapshot + outbox commit together; caller checkpoints before SMTP.
    with store.db:
        for item in updates:
            store.db.execute('INSERT OR IGNORE INTO announcements VALUES (?,?,\'pending\',?,?)',
                             (KEY,item['_id'],json.dumps(item),now.isoformat()))
        store.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(META,json.dumps(data,sort_keys=True)))
    return {'failed':errors,'events':len(data['events']),'queued':len(updates),'feeds':data['feeds']}
