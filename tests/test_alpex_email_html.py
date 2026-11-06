from datetime import datetime, timezone
from unittest.mock import patch
import json
from pathlib import Path
import pytest
from src.email_sender import EmailSender
from src.alpex_monitor import message

NOW=datetime(2026,11,6,11,13,tzinfo=timezone.utc)

def trade(name='Faraday <script>bad()</script>'):
 return {'kind':'bulk','source_id':'bulk:1','symbol':'ALPEXSOLAR','published_at':'2026-11-04',
  'event_date':'2026-11-04','participant':name,'side':'BUY','quantity':'148000','price':'663.26',
  'calculated_value_inr':'98162480.00','source_url':'https://www.nseindia.com/report-detail/display-bulk-and-block-deals','details':{}}

def summary():
 return message('redesign','[30-day summary]','Plain fallback',NOW,view={
  'type':'summary','period_start':'2026-10-08','period_end':'2026-11-06',
  'events':[trade()], 'history':[], 'coverage':{'bulk':{'error':None},'pit':{'error':'blocked'}}})

@pytest.fixture(autouse=True)
def env(monkeypatch):
 monkeypatch.setenv('EMAIL_SENDER','sender@example.com');monkeypatch.setenv('EMAIL_RECEIVER','receiver@example.com');monkeypatch.setenv('EMAIL_PASSWORD','test')

def sent(items):
 with patch('src.email_sender.smtplib.SMTP_SSL') as smtp:
  conn=smtp.return_value.__enter__.return_value;conn.send_message.return_value={}
  sender=EmailSender();sender.send(items);sender.close()
  return conn.send_message.call_args.args[0]

def test_summary_is_multipart_html_with_tables_and_preserves_precision():
 msg=sent([summary()])
 assert msg.get_content_type()=='multipart/alternative'
 html=msg.get_body(preferencelist=('html',)).get_content()
 assert '1,48,000' in html and '663.26' in html and '9.82' in html
 assert '<table' in html and 'Alpex Solar' in html and 'Trade &amp; ownership' in html
 assert '<script' not in html.lower() and '&lt;script&gt;' in html.lower()
 assert 'Unavailable' in html and 'Insider' in html
 assert 'https://www.nseindia.com/' in html
 assert msg.get_body(preferencelist=('plain',))
 assert len(html.encode())<90000

def test_batch_also_has_html_without_losing_any_alert():
 msg=sent([summary(),message('two','[Coverage warning] Bulk deals','Collection unavailable',NOW)])
 html=msg.get_body(preferencelist=('html',))
 assert html and 'Collection unavailable' in html.get_content() and '663.26' in html.get_content()
 assert 'Plain fallback' in msg.get_body(preferencelist=('plain',)).get_content()

def test_ordinary_announcement_remains_compatible():
 msg=sent([{'_id':'x','_company_name':'Other','_subject':'Filing','_exchange':'NSE','_published':'2026-11-06','_page_url':'https://example.com'}])
 assert msg.get_content_type()=='text/plain' and 'Filing' in msg['Subject']

def test_snapshot_is_readable_percentages_and_not_xbrl_keys():
 from src.alpex_email import render
 a={'kind':'shareholding','source_id':'s1','published_at':'2026-07-21T10:00:00','event_date':'30-JUN-2026','document_url':'https://nsearchives.nseindia.com/old.xml','source_url':'https://www.nseindia.com/','details':{'holdings':{'category: InstitutionsForeign':{'NumberOfShares':'798300','ShareholdingAsAPercentageOfTotalNumberOfShares':'0.0313'}}}}
 b={**a,'source_id':'s2','published_at':'2026-10-16T14:16:35','event_date':'30-SEP-2026','details':{'holdings':{'category: InstitutionsForeign':{'NumberOfShares':'838300','ShareholdingAsAPercentageOfTotalNumberOfShares':'0.0328'}}}}
 item=message('s','Shareholding','Fallback',NOW,view={'type':'events','events':[b],'history':[a,b]})
 html=render([item])
 assert '3.13%' in html and '3.28%' in html and '+0.15' in html
 assert 'Foreign institutions' in html and 'ShareholdingAsAPercentage' not in html
 assert 'https://nsearchives.nseindia.com/old.xml' in html

def test_summary_suppresses_stale_versions_of_same_source():
 from src.alpex_email import summary_message
 a=trade('Fund');b={**a,'price':'664','_version':'new','detected_at':'2026-11-06T12:00:00+00:00'}
 data={'events':[a,b],'feeds':{},'activated_at':NOW.isoformat()}
 item=summary_message(data,now=NOW)
 assert len(item['_alpex_view']['events'])==1
 assert item['_alpex_view']['events'][0]['price']=='664'

def test_newly_disclosed_and_missing_holders_remain_visible_without_entry_exit_claims():
 from src.alpex_email import ownership_card
 def snap(period,holdings):return {'kind':'shareholding','source_id':period,'event_date':period,'published_at':'2026-10-16T10:00:00','details':{'holdings':holdings},'source_url':'https://www.nseindia.com/'}
 old=snap('30-JUN-2026',{'holder: OLD FUND':{'NumberOfShares':'100000','ShareholdingAsAPercentageOfTotalNumberOfShares':'0.06'}})
 new=snap('30-SEP-2026',{'holder: NEW FUND':{'NumberOfShares':'100000','ShareholdingAsAPercentageOfTotalNumberOfShares':'0.06'}})
 html=ownership_card(new,[old,new])
 assert 'New Fund' in html and 'Old Fund' in html
 assert 'Newly disclosed' in html and 'No longer disclosed' in html
 assert '1,00,000' in html and '6.00%' in html
 assert 'not proof' in html.lower()

def test_insider_reported_value_keeps_paise():
 from src.alpex_email import disclosure_card
 event={'kind':'pit','published_at':'2026-11-06','event_date':'2026-11-04','source_url':'https://www.nseindia.com/','details':{'participants':[{'NameOfThePerson':'Fund','SecuritiesAcquiredOrDisposedValueOfSecurity':'100000.45'}]}}
 assert '1,00,000.45' in disclosure_card(event)
