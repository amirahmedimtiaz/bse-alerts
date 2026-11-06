from datetime import date
from pathlib import Path
from decimal import Decimal
import pytest
from src import alpex_sources as s

FIX = Path(__file__).parent / 'fixtures/alpex'

def test_bulk_full_precision_and_both_sides():
    rows = s.parse_deals((FIX/'bulk.csv').read_text(), 'bulk')
    assert len(rows) == 2
    assert rows[0]['quantity'] == '148000'
    assert rows[0]['price'] == '663.26'
    assert rows[0]['calculated_value_inr'] == str(Decimal(148000)*Decimal('663.26'))
    assert rows[1]['price'] == '663.28'
    assert rows[0]['event_date'] == '2026-11-04'

@pytest.mark.parametrize('content', ['<html>blocked</html>', '{}', 'Date,Symbol\n'])
def test_invalid_csv_fails_closed(content):
    with pytest.raises(ValueError): s.parse_deals(content, 'bulk')

def test_all_rows_not_table_limit():
    content=(FIX/'bulk.csv').read_text().splitlines()
    assert len(s.parse_deals('\n'.join([content[0]]+[content[1]]*80),'bulk')) == 80

def test_pit_xbrl_preserves_transfer_mode_and_multiple_people():
    rows=s.xml_details((FIX/'pit.xml').read_text(), 'MACPOWER')
    assert len(rows['participants']) == 2
    assert rows['participants'][0]['ModeOfAcquisitionOrDisposal'] == 'Inter-se-Transfer'
    assert 'PAN' not in str(rows)
    with pytest.raises(ValueError,match='symbol'): s.xml_details((FIX/'pit.xml').read_text(),'ALPEXSOLAR')

def test_empty_json_needs_explicit_table():
    assert s.table({'data': []}) == []
    assert s.table([]) == []
    with pytest.raises(ValueError): s.table({'error':'blocked'})

def test_fetch_uses_sme_and_full_download():
    seen=[]
    def read(url):
        seen.append(url)
        return (FIX/'bulk.csv').read_text() if 'csv=true' in url else '{"data":[]}'
    rows=s.fetch_feed('bulk',date(2026,10,8),date(2026,11,6),read=read)
    assert len(rows)==2 and 'historicalOR' in seen[0] and 'csv=true' in seen[0]
    assert s.fetch_feed('pit',date(2026,10,8),date(2026,11,6),read=read)==[]
    assert 'index=sme' in seen[-1]

def test_out_of_range_or_wrong_symbol_is_rejected():
    text=(FIX/'bulk.csv').read_text().replace('04-NOV-2026','04-NOV-2025')
    with pytest.raises(ValueError,match='range'):
        s.fetch_feed('bulk',date(2026,10,8),date(2026,11,6),read=lambda _:text)

def test_event_version_stable_and_changes_with_price():
    a=s.parse_deals((FIX/'bulk.csv').read_text(),'bulk')[0]
    assert s.version(a)==s.version(dict(reversed(list(a.items()))))
    assert s.version(a)!=s.version({**a,'price':'664'})

def test_version_ignores_download_date_window():
    a=s.parse_deals((FIX/'bulk.csv').read_text(),'bulk')[0]
    assert s.version(a)==s.version({**a,'source_url':a['source_url']+'?from=01-11-2026'})

def test_capital_history_filters_and_preserves_source_wording():
    import json
    rows=[{'symbol':'ALPEXSOLAR','seq_id':'x','sort_date':'2026-11-04 12:00:00',
        'desc':'Allotment of Securities','attchmntText':'Conversion of warrants','attchmntFile':'https://nsearchives.nseindia.com/a.pdf'},
        {'symbol':'ALPEXSOLAR','seq_id':'y','sort_date':'2026-10-01 12:00:00','desc':'Allotment of Securities'}]
    result=s.fetch_feed('capital',date(2026,10,8),date(2026,11,6),read=lambda _:json.dumps(rows))
    assert len(result)==1 and result[0]['details']['headline']=='Conversion of warrants'

def test_anonymous_typed_contexts_are_not_compared_by_row_number():
    text='''<xbrl xmlns:x="urn:x"><x:context id="Anonymous_Context15"><x:typedMember/></x:context><x:Symbol contextRef="MainI">ALPEXSOLAR</x:Symbol><x:NumberOfShares contextRef="Anonymous_Context15">100</x:NumberOfShares></xbrl>'''
    result=s.xml_details(text)
    assert not result['holdings']
    assert result['unmatched_contexts']==['Anonymous_Context15']
