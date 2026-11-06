"""Email-client-safe Alpex layouts. All source content is escaped; no remote assets."""
from __future__ import annotations
import html
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from .alpex_sources import iso, PAGES

INK='#15363c'; MUTED='#677b80'; LINE='#e1e9e9'; GREEN='#087660'
LABELS={'bulk':'Bulk deals','block':'Block deals','pit':'Insider disclosures','sast':'SAST disclosures',
        'pledge':'Promoter pledges','shareholding':'Shareholding','capital':'Capital filings'}

def esc(value):return html.escape(str(value),quote=True)

def day(value):
    try:return datetime.fromisoformat(iso(str(value)[:19])).strftime('%d %b %Y').lstrip('0')
    except (ValueError,TypeError):return str(value or 'Not disclosed')

def integer(value):
    try:
        n=format(Decimal(str(value)),'.0f'); sign='−' if n.startswith('-') else '';n=n.lstrip('-')
        if len(n)<=3:return sign+n
        head=n[:-3]; groups=[]
        while head:groups.insert(0,head[-2:]);head=head[:-2]
        return sign+','.join(groups+[n[-3:]])
    except (InvalidOperation,ValueError):return str(value or '—')

def money(value):
    try:
        raw=format(Decimal(str(value)), 'f')
        whole,sep,fraction=raw.partition('.')
        return integer(whole)+(sep+fraction if sep else '')
    except (InvalidOperation,ValueError):return str(value or '—')

def pct(value):
    try:return f'{Decimal(str(value))*100:.2f}%'
    except (InvalidOperation,ValueError):return '—'

def change(a,b):
    try:
        delta=(Decimal(str(b))-Decimal(str(a)))*100
        return ('+' if delta>0 else '−' if delta<0 else '')+f'{abs(delta):.2f} pp'
    except (InvalidOperation,ValueError):return '—'

def link(url,label):
    parsed=urlparse(str(url or ''))
    if parsed.scheme!='https' or parsed.hostname not in ('www.nseindia.com','nsearchives.nseindia.com','archives.nseindia.com'):return ''
    return f'<a href="{esc(url)}" style="color:{GREEN};text-decoration:none;font-size:13px;font-weight:bold;">{esc(label)} &rarr;</a>'

def paragraph(text,small=False):
    return f'<p style="margin:10px 0;color:{MUTED if small else INK};font-size:{12 if small else 14}px;line-height:1.65;">{esc(text)}</p>'

def heading(kicker,title):
    return f'<p style="margin:0 0 8px;color:{GREEN};font-size:10px;letter-spacing:2px;font-weight:bold;text-transform:uppercase;">{esc(kicker)}</p><h2 style="margin:0 0 18px;font:700 23px Georgia,serif;color:{INK};">{esc(title)}</h2>'

def section(content):
    return f'<tr><td class="pad" style="padding:28px 36px;border-bottom:1px solid {LINE};">{content}</td></tr>'

def active(history):
    latest={}
    for event in history:latest[event['source_id']]=event
    return list(latest.values())

def previous_snapshot(event,history):
    prior=[e for e in active(history) if e['kind']=='shareholding' and iso(e['event_date'])<iso(event['event_date'])]
    return max(prior,key=lambda e:(iso(e['event_date']),e['published_at']),default=None)

def participant_name(name):
    words=str(name).split()
    return ' '.join(w if w.strip('()-,').upper() in ('OEIC','PCC','PC','LLP','HUF','LLC') else w.title() for w in words)

def trade_card(event):
    buy=event.get('side')=='BUY'; badge='#e4f3ec' if buy else '#fcf0df'; accent=GREEN if buy else '#936020'
    try:value=f"₹{Decimal(event['calculated_value_inr'])/Decimal(10000000):.2f} cr"
    except (KeyError,InvalidOperation):value='Not disclosed'
    metrics=[('SHARES',integer(event.get('quantity'))),('AVG. PRICE','₹'+str(event.get('price','—'))),('APPROX. VALUE',value)]
    cells=''.join(f'<td width="33%" style="vertical-align:top;padding:14px 8px 2px 0;"><div style="font-size:9px;letter-spacing:.8px;color:{MUTED};margin-bottom:5px;">{label}</div><div style="font-size:18px;font-weight:bold;color:{INK};white-space:nowrap;">{esc(val)}</div></td>' for label,val in metrics)
    return f'''<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 14px;border:1px solid {LINE};border-radius:10px;background:#fff;"><tr><td style="padding:18px 20px;"><table role="presentation" width="100%"><tr><td style="font-size:12px;color:{MUTED};">{esc(day(event.get('event_date')))} &nbsp;·&nbsp; {esc(LABELS.get(event['kind'],'Deal'))}</td><td align="right"><span style="padding:5px 10px;background:{badge};color:{accent};font-size:10px;letter-spacing:.8px;font-weight:bold;border-radius:4px;">{esc(event.get('side',''))}</span></td></tr></table><p style="font-size:15px;font-weight:bold;line-height:1.5;margin:13px 0 0;color:{INK};">{esc(participant_name(event.get('participant','Participant')))}</p><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>{cells}</tr></table><p style="margin:15px 0 0;">{link(event.get('source_url'),'View NSE disclosure')}</p></td></tr></table>'''


def ownership_card(event,history):
    old=previous_snapshot(event,history);a=(old or {}).get('details',{}).get('holdings',{});b=event['details'].get('holdings',{})
    categories=[('Promoter & group','ShareholdingOfPromoterAndPromoterGroup'),('Public','PublicShareholding'),
                ('Foreign institutions','InstitutionsForeign'),('Domestic institutions','InstitutionsDomestic')]
    field='ShareholdingAsAPercentageOfTotalNumberOfShares'; rows=[]
    for label,key in categories:
        before=a.get('category: '+key,{});after=b.get('category: '+key,{})
        if not after:continue
        delta=change(before.get(field),after.get(field))
        rows.append((label,pct(before.get(field)),pct(after.get(field)),delta))
    if not rows:
        # A schema containing named holders only can still be displayed accurately.
        for label,after in b.items():
            if label in a and a[label]!=after:
                rows.append((label.removeprefix('holder: '),pct(a[label].get(field)),pct(after.get(field)),change(a[label].get(field),after.get(field))))
    labels=['HOLDING',day(old['event_date']) if old else 'PRIOR',day(event['event_date']),'CHANGE']
    th=''.join(f'<th align="{ "left" if i==0 else "right"}" style="padding:12px 5px;border-bottom:2px solid {LINE};font-size:9px;letter-spacing:.4px;color:{MUTED};font-weight:600;">{esc(label)}</th>' for i,label in enumerate(labels))
    body=''.join('<tr>'+''.join(f'<td align="{"left" if i==0 else "right"}" style="padding:13px 5px;border-bottom:1px solid {LINE};font-size:12px;line-height:1.4;{ "font-weight:600;" if i==0 else "white-space:nowrap;"}color:{INK};">{esc(v)}</td>' for i,v in enumerate(row))+'</tr>' for row in rows)
    content=heading('Ownership snapshot','Who holds the shares')
    content+=paragraph(f"As of {day(event['event_date'])} · Filed {day(event['published_at'])}",True)
    content+=f'<table width="100%" cellspacing="0" cellpadding="0"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'
    if not old:content+=paragraph('A compatible prior snapshot is unavailable. Changes are not calculated.',True)
    # Show material named-holder movements separately, without conflating names and category totals.
    named=[(label,a[label],after) for label,after in b.items() if label.startswith('holder: ') and label in a and a[label].get('NumberOfShares')!=after.get('NumberOfShares')]
    if named:
        content+=paragraph('Disclosed holder changes')
        for label,before,after in named:
            content+=paragraph(participant_name(label[8:])+': '+integer(before.get('NumberOfShares'))+' → '+integer(after.get('NumberOfShares'))+' shares',True)
    if old:
        added=[label for label in b.keys()-a.keys() if label.startswith('holder: ')]
        removed=[label for label in a.keys()-b.keys() if label.startswith('holder: ')]
        if added or removed:
            content+=paragraph('Changes in disclosed holder names')
            for labels,source,status in ((added,b,'Newly disclosed'),(removed,a,'No longer disclosed')):
                for label in sorted(labels):
                    facts=source[label]
                    content+=paragraph(status+': '+participant_name(label[8:])+' · '+integer(facts.get('NumberOfShares'))+' shares · '+pct(facts.get(field)),True)
            content+=paragraph('Newly disclosed or missing names are not proof of a new investment or complete exit.',True)
    content+=paragraph('Percentage-point changes are calculated from reported snapshots. Public includes institutional holdings; these rows should not be added together. Snapshot changes do not establish market purchases or sales.',True)
    if event['details'].get('unmatched_contexts'):content+=paragraph('Unnamed filing rows are excluded from comparisons.',True)
    content+='<p style="margin:16px 0 0;">'+link(event.get('document_url') or event['source_url'],'Current filing')
    if old:content+=' &nbsp;&nbsp; '+link(old.get('document_url') or old['source_url'],'Previous filing')
    return content+'</p>'


def disclosure_card(event):
    details=event.get('details',{});kind=event['kind']
    content=heading('New disclosure',LABELS.get(kind,'Company filing'))
    content+=paragraph('Filed '+day(event.get('published_at'))+' · Transaction / period: '+str(event.get('event_date') or 'See source'),True)
    fields={'acquirerName':'Investor','acquisitionMode':'Transaction mode','acqSaleType':'Action','noOfShareAcq':'Shares acquired',
       'noOfShareSale':'Shares sold','noOfShareAft':'Shares after transaction','totAftShare':'Holding after (%)',
       'promoterName':'Promoter','eventDetailsType':'Event','eventDetailsTypeEncumb':'Encumbrance',
       'eventDetailsHolding':'Affected shares','postEventHolding':'Encumbered shares after event',
       'NameOfThePerson':'Person','CategoryOfPerson':'Category','ModeOfAcquisitionOrDisposal':'Transaction mode',
       'SecuritiesAcquiredOrDisposedTransactionType':'Action','SecuritiesAcquiredOrDisposedNumberOfSecurity':'Shares',
       'SecuritiesAcquiredOrDisposedValueOfSecurity':'Reported value (₹)',
       'SecuritiesHeldPriorToAcquisitionOrDisposalNumberOfSecurity':'Shares before',
       'SecuritiesHeldPostAcquistionOrDisposalNumberOfSecurity':'Shares after'}
    for group in [details]+details.get('participants',[]):
        for key,label in fields.items():
            if group.get(key) is not None:
                val=group[key]
                if 'value' in label.lower():val=money(val)
                elif 'shares' in label.lower():val=integer(val)
                content+=f'<p style="margin:8px 0;font-size:14px;line-height:1.6;"><span style="color:{MUTED};">{esc(label)}:</span> <strong>{esc(val)}</strong></p>'
    if kind=='capital':
        content+=paragraph(details.get('subject') or 'Capital-related company filing')
        if details.get('headline'):content+=paragraph(details['headline'])
        content+=paragraph('A proposal or approval does not by itself establish completed issuance.',True)
    content+='<p style="margin:16px 0 0;">'+link(event.get('document_url') or event.get('source_url'),'Read the official filing')+'</p>'
    return content


def coverage_card(coverage):
    if not coverage:return ''
    okay=sum(not v.get('error') for v in coverage.values());total=len(coverage)
    content=f'<table role="presentation" width="100%"><tr><td><p style="margin:0;color:{INK};font-size:15px;font-weight:bold;">Source coverage</p></td><td align="right" style="font-size:12px;color:{GREEN if okay==total else "#936020"};font-weight:bold;">{okay} / {total} available</td></tr></table>'
    content+='<p style="margin:12px 0 0;line-height:2;font-size:11px;color:'+MUTED+';">'
    content+=' &nbsp;·&nbsp; '.join(esc(LABELS.get(k,k))+(' — <strong style="color:#936020;">Unavailable</strong>' if v.get('error') else ' — checked') for k,v in coverage.items())+'</p>'
    if okay<total:content+=paragraph('Unavailable sources have unknown coverage. An empty successful result is not the same as an unavailable feed.',True)
    return content


def summary_message(data,*,now=None):
    from .alpex_monitor import message,describe
    now=now or datetime.now(timezone.utc);today=now.astimezone(ZoneInfo('Asia/Kolkata')).date();start=today-timedelta(days=29)
    history=active(data['events']);events=[e for e in history if start.isoformat()<=e['published_at'][:10]<=today.isoformat()]
    view={'type':'summary','period_start':start.isoformat(),'period_end':today.isoformat(),'events':events,'history':history,'coverage':data['feeds']}
    body=f'Trade and ownership brief · {day(start)} – {day(today)}\n\n'+'\n\n'.join(describe(e,history) for e in events)
    return message('formatted-summary-v1:'+now.isoformat(),'Trade & ownership brief · '+day(start)+' – '+day(today),body,now,view=view)


def render(announcements):
    first=announcements[0]; views=[item.get('_alpex_view',{}) for item in announcements]
    summary=next((v for v in views if v.get('type')=='summary'),None)
    subtitle=(day(summary['period_start'])+' — '+day(summary['period_end'])) if summary else 'Official exchange disclosures'
    stamp=first.get('_published','')
    try:stamp=datetime.fromisoformat(stamp).astimezone(ZoneInfo('Asia/Tokyo')).strftime('%d %b %Y · %H:%M JST')
    except ValueError:pass
    preheader='Alpex Solar — disclosed trades, ownership changes and official source links.'
    content=''
    if summary:
        events=summary.get('events',[]);deals=sum(e['kind'] in ('bulk','block') for e in events);filings=sum(e['kind'] not in ('bulk','block') for e in events)
        tiles=''.join(f'<td width="50%" style="padding:18px 20px;vertical-align:top;{ "border-right:1px solid "+LINE+";" if i==0 else ""}"><div style="font:700 32px Georgia,serif;color:{INK};">{num:02d}</div><div style="font-size:12px;color:{MUTED};margin-top:6px;">{label}</div></td>' for i,(num,label) in enumerate([(deals,'reported deal sides'),(filings,'ownership / capital filings')]))
        content+=section(f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f1f6f4;border-radius:8px;"><tr>{tiles}</tr></table>'+paragraph('Your 30-day brief, drawn from official NSE disclosures. Prices retain the exchange’s reported precision.',True))
    for item,view in zip(announcements,views):
        if not view:
            # Backward-compatible rendering of already queued alerts and coverage notices.
            title=re.sub(r'\[ALPEX\]\s*','',item.get('_subject','Update'))
            safe=esc(item.get('_body','')).replace('\n','<br>')
            content+=section(heading('Monitor update',title)+f'<p style="font-size:14px;line-height:1.7;overflow-wrap:anywhere;">{safe}</p>')
            continue
        events=view.get('events',[]);history=view.get('history',[])
        if '[Correction]' in item.get('_subject',''):
            content+=section(paragraph('CORRECTED DISCLOSURE — the source changed previously reported information. Earlier evidence remains in the archive.'))
        trades=[e for e in events if e['kind'] in ('bulk','block')]
        if trades:
            content+=section(heading('Disclosed transactions','The latest reported trades')+''.join(trade_card(e) for e in trades)+paragraph('Values are approximate: shares × reported average price. Each row is one reported side; these disclosures do not establish a matched buyer and seller.',True))
        for event in sorted((e for e in events if e['kind'] not in ('bulk','block')),key=lambda e:e['published_at'],reverse=True):
            content+=section(ownership_card(event,history) if event['kind']=='shareholding' else disclosure_card(event))
        if not events:content+=section(paragraph('No matching disclosures were found in the successfully checked sources for this period.'))
        if view.get('coverage'):content+=section(coverage_card(view['coverage']))
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Alpex Solar — Trade &amp; ownership brief</title><style>@media only screen and (max-width:600px){{.pad{{padding:24px 18px!important}}.outer{{padding:12px 6px!important}}.hero{{font-size:32px!important}}}}</style></head><body style="margin:0;padding:0;background:#edf2f3;font-family:Arial,Helvetica,sans-serif;color:{INK};"><div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{esc(preheader)}</div><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#edf2f3;"><tr><td class="outer" align="center" style="padding:32px 12px;"><table role="presentation" width="680" cellspacing="0" cellpadding="0" style="width:100%;max-width:680px;background:#fff;border-collapse:separate;border-spacing:0;border-radius:14px;overflow:hidden;"><tr><td style="height:5px;background:#c3a56d;font-size:0;line-height:0;">&nbsp;</td></tr><tr><td class="pad" style="padding:32px 36px;background:{INK};"><p style="margin:0 0 20px;color:#aecfc5;font-size:10px;letter-spacing:2.6px;font-weight:bold;">COMPANY WATCH &nbsp;/&nbsp; NSE: ALPEXSOLAR</p><h1 class="hero" style="margin:0;color:#fff;font:400 40px Georgia,serif;letter-spacing:-.7px;">Alpex Solar</h1><p style="margin:12px 0 0;font-size:15px;color:#d6e6e2;">Trade &amp; ownership brief</p><p style="margin:22px 0 0;font-size:12px;color:#aec6c3;">{esc(subtitle)}</p></td></tr>{content}<tr><td class="pad" style="padding:24px 36px;background:#f7f9f9;"><p style="margin:0;font-size:11px;line-height:1.8;color:{MUTED};"><strong style="color:{INK};">Read this as disclosed activity.</strong> Public reports can be delayed or revised and do not reveal every investor’s trades or complete current holdings. Shareholding changes may also reflect transfers, dilution or reclassification.</p><p style="margin:14px 0 0;">{link(PAGES['bulk'],'NSE deals archive')} &nbsp;&nbsp; {link(PAGES['shareholding'],'Shareholding filings')}</p><p style="margin:18px 0 0;font-size:10px;color:#819297;">Prepared {esc(stamp)} · Scheduled checks every 5 minutes</p></td></tr></table><p style="margin:16px 0 0;font-size:10px;letter-spacing:1px;color:#84979b;">ALPEX SOLAR &nbsp;·&nbsp; OFFICIAL-SOURCE MONITOR</p></td></tr></table></body></html>'''
