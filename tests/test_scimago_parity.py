"""Executed synthetic comparisons with the unchanged legacy SourceIndex."""
from venue_fixtures import VenueCase,issn,build_package
from reprovane.scimago import ScimagoRecord,SourceIndex,relevant_category_summary,enrich_records
from reprovane.venue_resolution.normalization import title_normalized
from reprovane.venue_resolution.projection import ScimagoVenueProjection
from reprovane.providers.scimago import import_scimago_source_snapshot


def legacy_record(sid,title,identifier):
    return ScimagoRecord(sourceid=sid,year=2023,title=title,source_type='journal',issn=identifier,publisher='Synthetic',
        open_access='No',open_access_diamond='No',sjr=1.0,best_quartile='Q1',h_index=10,country='VN',region='',coverage='',categories='Software (Q1)',areas='',rank=1)


class TestScimagoParity(VenueCase):
    def compare(self,left_title,right_title,left_issn='',right_issn='',linking=False):
        legacy=SourceIndex([legacy_record('100',right_title,right_issn)]).match(left_title,'' if linking else left_issn,left_issn if linking else '')
        records=[self.record(title=left_title,identifier=left_issn or None,namespace='issn_l' if linking else 'issn'),self.record(title=right_title,provider='scimago',native='100',identifier=right_issn or None)]
        rid=self.ready(records); rows=self.store.list_active_decisions(rid); new=rows[0]['decision_type'] if rows else 'UNMATCHED'
        return legacy,new
    def test_issn_exact_parity(self):
        old,new=self.compare('Different','Journal',issn(),issn()); self.assertEqual((old.status,new),('MATCHED','SAME'))
    def test_linking_parity(self):
        old,new=self.compare('Different','Journal',issn(),issn(),True); self.assertEqual((old.status,new),('MATCHED','SAME'))
    def test_title_only_parity(self):
        old,new=self.compare('Journal','Journal'); self.assertEqual((old.status,new),('MATCHED','SAME'))
    def test_normalized_title_parity(self):
        old,new=self.compare('Alpha-Journal','Alpha Journal'); self.assertEqual((old.status,new),('MATCHED','SAME'))
    def test_ampersand_parity(self):
        old,new=self.compare('A & B','A and B'); self.assertEqual((old.status,new),('MATCHED','SAME'))
    def test_status_suffix_parity(self):
        old,new=self.compare('Journal','Journal (discontinued)'); self.assertEqual((old.status,new),('MATCHED','SAME'))
    def test_conflicting_valid_issn_parity(self):
        old,new=self.compare('Journal','Journal',issn(),issn('7654321')); self.assertEqual((old.status,new),('UNMATCHED','AMBIGUOUS'))
    def test_invalid_checksum_intentional_safety_change(self):
        old,new=self.compare('Different','Journal','12345678','12345678'); self.assertEqual((old.status,new),('MATCHED','UNMATCHED'))
    def test_category_equality_parity(self):
        text='Information Systems (Q2); Management Information Systems (Q1)'
        self.assertEqual(relevant_category_summary(text,['Information Systems'])[1],'Q2')
        self.assertNotEqual(title_normalized('Information Systems'),title_normalized('Management Information Systems'))
    def test_ambiguous_same_level_parity(self):
        old=SourceIndex([legacy_record(s,'Shared','') for s in ['100','200']]).match('Shared','','')
        rid=self.ready([self.record(title='Shared')]+[self.record(title='Shared',provider='scimago',native=s) for s in ['100','200']])
        self.assertEqual(old.status,'AMBIGUOUS'); self.assertEqual({r['decision_type'] for r in self.store.list_active_decisions(rid)},{'AMBIGUOUS'})
    def temporal_compare(self,source='Old name',year=2022,dash=False):
        root=self.root/'package'; rows={2022:[{'Sourceid':'100','Title':'Old name','Issn':issn(),'SJR Best Quartile':'Q2'},{'Sourceid':'200','Title':'Historical','Issn':issn('7654321')}],2023:[{'Sourceid':'100','Title':'New name','Issn':issn('1111111'),'SJR Best Quartile':'-' if dash else 'Q1'}]}
        manifest=build_package(root,version=1,rows=rows)
        query=dict(source=source,publication_year=str(year),issn='',issn_l='')
        legacy_manifest=dict(manifest,journal_value={'year':2022,'file':'not-provided.csv'})
        old=enrich_records([query],root,legacy_manifest,['Information Systems'])[0]
        imported=[import_scimago_source_snapshot(self.db,root,y) for y in [2022,2023]]
        external=self.record(title=source); rid=self.ready([external]+[r['provider_record_id'] for result in imported for r in result['provider_records']],True)
        node=next(m['provider_node_id'] for m in self.store.list_provider_node_members(rid) if m['provider_record_id']==external)
        entity=next(m['venue_entity_id'] for m in self.store.list_entity_members(rid) if m['provider_node_id']==node)
        p=ScimagoVenueProjection(self.store)
        def quartile(metrics): return next((m['value_text'] for m in metrics if m['metric_code']=='BEST_QUARTILE'),'')
        return old,dict(presence=p.scimago_presence_status(rid,entity),legacy_status=p.legacy_status(p.scimago_presence_status(rid,entity)),years=p.scimago_years_present(rid,entity),exact_quartile=quartile(p.scimago_metrics_for_year(rid,entity,year)),latest_quartile=quartile(p.scimago_metrics_latest_snapshot(rid,entity)),categories=p.category_quartile_exact(rid,entity,year,['Information Systems']))
    def test_same_sourceid_latest_recovery_parity(self):
        old,new=self.temporal_compare(); self.assertEqual((old['source_status'],new['legacy_status']),('ACTIVE_LATEST','ACTIVE_LATEST')); self.assertEqual(new['years'],[2022,2023])
    def test_historical_only_parity(self):
        old,new=self.temporal_compare('Historical'); self.assertEqual((old['source_status'],new['legacy_status']),('HISTORICAL_ONLY','HISTORICAL_ONLY')); self.assertEqual(new['latest_quartile'],'')
    def test_exact_publication_and_global_latest_metrics_parity(self):
        old,new=self.temporal_compare(); self.assertEqual((old['best_quartile_at_publication'],new['exact_quartile']),('Q2','Q2')); self.assertEqual((old['best_quartile_latest'],new['latest_quartile']),('Q1','Q1'))
    def test_future_year_no_publication_metric_parity(self):
        old,new=self.temporal_compare(year=2030); self.assertEqual((old['best_quartile_at_publication'],new['exact_quartile']),('','')); self.assertEqual(old['source_status'],new['legacy_status'])
    def test_dash_quartile_unavailable_parity(self):
        old,new=self.temporal_compare('New name',2023,True); self.assertEqual((old['best_quartile_at_publication'],new['exact_quartile']),('','')); self.assertEqual((old['best_quartile_latest'],new['latest_quartile']),('',''))
    def test_temporal_category_equality_parity(self):
        old,new=self.temporal_compare(); self.assertEqual(old['relevant_category_quartile_at_publication'],new['categories']['best_quartile'])
