from datetime import datetime,timedelta,timezone
from unittest.mock import Mock
import json
import pytest
from src.alert_store import AlertStore
from src import alpex_monitor as m
from src.scanner import deliver

NOW=datetime(2026,11,6,8,tzinfo=timezone.utc)
def event(price='663.26'):
 return {'kind':'bulk','symbol':'ALPEXSOLAR','source_id':'bulk:4:Faraday:BUY','published_at':'2026-11-04',
 'event_date':'2026-11-04','participant':'Faraday','side':'BUY','quantity':'148000','price':price,
 'calculated_value_inr':'98162480.00','source_url':'https://www.nseindia.com/test','details':{}}
def fetch(kind,start,end): return [event()] if kind=='bulk' else []
@pytest.fixture
def store():
 s=AlertStore(':memory:'); yield s; s.close()

def test_initial_summary_restart_dedupe_and_correction(store):
 m.collect(store,now=NOW,fetch=fetch)
 pending=store.pending(); assert len(pending)==1
 assert '30-day' in pending[0]['announcement']['_subject']
 assert '663.26' in pending[0]['announcement']['_body']
 sender=Mock();sender.send.return_value='msg1'
 deliver(store,lambda:None,sender=sender)
 restored=AlertStore(':memory:');restored.restore(json.loads(json.dumps(store.export())))
 m.collect(restored,now=NOW+timedelta(minutes=5),fetch=fetch)
 assert restored.pending_count()==0
 m.collect(restored,now=NOW+timedelta(minutes=10),fetch=lambda kind,*_: [event('664')] if kind=='bulk' else [])
 assert restored.pending_count()==1
 assert 'Correction' in restored.pending()[0]['announcement']['_subject']
 history=m.state(restored)['events']; assert len(history)==2
 restored.close()

def test_failed_initial_summary_is_retained(store):
 m.collect(store,now=NOW,fetch=fetch)
 sender=Mock();sender.send.side_effect=RuntimeError('SMTP failed')
 assert deliver(store,lambda:None,sender=sender)['email_failed']
 m.collect(store,now=NOW+timedelta(days=1),fetch=lambda *_:[])
 assert store.pending_count()==1

def test_outage_watermark_warning_repeat_cap_and_recovery(store):
 m.collect(store,now=NOW,fetch=fetch)
 def broken(kind,*args):
  if kind=='bulk':raise ValueError('blocked')
  return []
 m.collect(store,now=NOW+timedelta(minutes=5),fetch=broken)
 last=m.state(store)['feeds']['bulk']['last_success']
 m.collect(store,now=NOW+timedelta(minutes=36),fetch=broken)
 assert any('Coverage warning' in x['announcement']['_subject'] for x in store.pending())
 count=store.pending_count()
 m.collect(store,now=NOW+timedelta(minutes=41),fetch=broken)
 assert store.pending_count()==count
 assert m.state(store)['feeds']['bulk']['last_success']==last
 m.collect(store,now=NOW+timedelta(minutes=46),fetch=fetch)
 assert any('Coverage restored' in x['announcement']['_subject'] for x in store.pending())

def test_long_outage_fetches_from_last_success_not_just_30days(store):
 m.collect(store,now=NOW,fetch=fetch);seen=[]
 def later(kind,start,end):seen.append(start);return []
 m.collect(store,now=NOW+timedelta(days=60),fetch=later)
 assert min(seen)<=NOW.date()

def test_shareholding_comparison_matches_names_and_reports_pp():
 old={'kind':'shareholding','event_date':'30-JUN-2026','details':{'holdings':{'holder: ABC':{'NumberOfShares':'100','ShareholdingAsAPercentageOfTotalNumberOfShares':'0.1'}}}}
 new={'kind':'shareholding','event_date':'30-SEP-2026','details':{'holdings':{'holder: ABC':{'NumberOfShares':'150','ShareholdingAsAPercentageOfTotalNumberOfShares':'0.15'}}}}
 text=m.holding_changes(new,old)
 assert '5.00 pp' in text and '100' in text and '150' in text
 assert 'trade' in text.lower()

def test_mixed_batch_keeps_alpex_subject_and_separate_other_alerts(store):
 m.collect(store,now=NOW,fetch=fetch)
 store.collect('other','2026-11-06',[])
 store.collect('other','2026-11-06',[{'_id':'other','_subject':'Other filing'}])
 sender=Mock();sender.send.side_effect=['one','two']
 assert deliver(store,lambda:None,sender=sender)['emails']==2
 for call in sender.send.call_args_list:
  assert len({bool(x.get('_alpex')) for x in call.args[0]})==1

def test_capital_uses_existing_alert_and_is_not_emailed_twice(store):
    m.collect(store,now=NOW,fetch=fetch)
    capital={'kind':'capital','symbol':'ALPEXSOLAR','source_id':'capital:new',
        'published_at':'2026-11-06T12:00:00','event_date':'2026-11-06',
        'source_url':'https://www.nseindia.com/test','details':{'headline':'Warrant allotment'}}
    before=store.pending_count()
    m.collect(store,now=NOW+timedelta(minutes=5),fetch=lambda kind,*_: [capital] if kind=='capital' else [])
    assert store.pending_count()==before
    assert any(e['kind']=='capital' for e in m.state(store)['events'])

def test_render_failure_does_not_consume_unsent_event(store,monkeypatch):
    m.collect(store,now=NOW,fetch=lambda *_:[])
    original=m.describe
    monkeypatch.setattr(m,'describe',lambda *args: (_ for _ in ()).throw(ValueError('unsupported facts')))
    m.collect(store,now=NOW+timedelta(minutes=5),fetch=fetch)
    assert not m.state(store)['events']
    monkeypatch.setattr(m,'describe',original)
    m.collect(store,now=NOW+timedelta(minutes=10),fetch=fetch)
    assert len(m.state(store)['events'])==1
    assert store.pending_count()==2

def test_same_id_capital_correction_alerts_even_when_existing_scanner_dedupes(store):
    def cap(word):
        return {'kind':'capital','symbol':'ALPEXSOLAR','source_id':'capital:same',
         'published_at':'2026-11-06T12:00:00','event_date':'2026-11-06',
         'source_url':'https://www.nseindia.com/test','details':{'headline':word}}
    m.collect(store,now=NOW,fetch=lambda kind,*_: [cap('original')] if kind=='capital' else [])
    m.collect(store,now=NOW+timedelta(minutes=5),fetch=lambda kind,*_: [cap('corrected')] if kind=='capital' else [])
    assert any('Correction' in x['announcement']['_subject'] for x in store.pending())

def test_comparison_uses_latest_revision_even_with_same_publication_time():
    def snapshot(shares,period):
        return {'kind':'shareholding','event_date':period,'published_at':'2026-07-10T10:00:00',
          'source_id':period,'details':{'holdings':{'holder: ABC':{'NumberOfShares':str(shares)}}},'source_url':'https://www.nseindia.com/test'}
    a=snapshot(100,'30-JUN-2026');b=snapshot(200,'30-JUN-2026');c=snapshot(300,'30-SEP-2026')
    assert '200 → 300' in m.describe(c,[a,b,c])

def test_source_reversion_is_a_new_correction_not_silently_skipped(store):
    m.collect(store,now=NOW,fetch=fetch)
    m.collect(store,now=NOW+timedelta(minutes=5),fetch=lambda kind,*_: [event('664')] if kind=='bulk' else [])
    m.collect(store,now=NOW+timedelta(minutes=10),fetch=fetch)
    assert len(m.state(store)['events'])==3
    assert store.pending_count()==3

def test_report_is_self_contained_escaped_and_exports_history(store,tmp_path):
    from src.alpex_report import export
    altered={**event(),'participant':'<script>bad()</script>'}
    m.collect(store,now=NOW,fetch=lambda kind,*_: [altered] if kind=='bulk' else [])
    path=export(m.state(store),tmp_path)
    assert '<script>bad()' not in path.read_text()
    assert '&lt;script&gt;' in path.read_text()
    assert json.loads(path.with_suffix('.json').read_text())['events']
    assert '663.26' in path.with_suffix('.csv').read_text()

def test_custom_email_keeps_alpex_subject_and_evidence(monkeypatch):
    from src.email_sender import EmailSender
    from unittest.mock import patch
    monkeypatch.setenv('EMAIL_SENDER','sender@example.com')
    monkeypatch.setenv('EMAIL_RECEIVER','receiver@example.com')
    monkeypatch.setenv('EMAIL_PASSWORD','test')
    with patch('src.email_sender.smtplib.SMTP_SSL') as smtp:
        connection=smtp.return_value.__enter__.return_value
        connection.send_message.return_value={}
        client=EmailSender()
        client.send([m.message('id','[30-day summary]','Source: https://www.nseindia.com/\n148000 @ 663.26',NOW)])
        client.close()
        msg=connection.send_message.call_args.args[0]
        assert msg['Subject']=='[ALPEX] [30-day summary]'
        plain = msg.get_body(preferencelist=('plain',)).get_content()
        assert '663.26' in plain and 'https://www.nseindia.com/' in plain
