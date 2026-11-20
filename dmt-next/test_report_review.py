import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core.yield_calc import calculate_yield_summary_by_process,calculate_yield_summary_many,build_yield_report_aggregation
from core.report_review import classify,reset_classification,unresolved_counts,export_issues,mismatch_rows,record_its_alert
from core.report_trace import SKI_UNKNOWN_ALLOCATED, trace_rows
from core.export_excel import export_report_to_2xlsx

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.patch=patch('core.report_review.DATA_DIR',self.root);self.patch.start();self.addCleanup(self.patch.stop)
        self.txt=self.root/'data.txt';self.ski=self.root/'final.ski';self.bbt=self.root/'bbt';self.bbt.mkdir()
    def fixture(self,codes=('1111','UNKNOWN','9999'),bbt=False,legacy=False):
        rows=''.join(f'RX-,RX-01,{i},1,T,{c}\n' for i,c in enumerate(codes,1))
        self.txt.write_text('1,RX-,RX-01\n'+rows+'RX-,RX-01,EOS\n')
        coords=list(range(1,len(codes)+1))+([4] if bbt else [])
        self.ski.write_text(''.join(f'RX-01,{i},1\n' for i in coords))
        if bbt:(self.bbt/'RX-01.rpt').write_text('X:004 Y:001 = 1 - OT14\n')
        return self.calculate(legacy)
    def calculate(self,legacy=False):
        if legacy:return calculate_yield_summary_many([self.txt],None,8,7,self.bbt,self.ski)
        return calculate_yield_summary_by_process([self.txt],[],[],[],None,8,7,self.bbt,self.ski)
    def test_ot14_removed_from_denominator_ng_and_defect_columns_but_verified(self):
        from openpyxl import load_workbook
        summary=self.fixture(('SM03',),True);report=build_yield_report_aggregation(summary,'M')
        self.assertEqual((summary['gross_total_units'],summary['total_units'],summary['defective_units'],summary['good_units']),(8,7,1,6))
        self.assertEqual(report['ot14_excluded_units'],1);self.assertNotIn('OT14',report['final_defects_by_code_including_bbt'])
        self.assertEqual(len(mismatch_rows(summary)),0)
        output=self.root/'report.xlsx';result=export_report_to_2xlsx(report,str(Path(__file__).resolve().parent.parent/'resources/templates/report.xlsx'),str(output))
        self.assertTrue(result['success'],result)
        book=load_workbook(output);sheet=book.active
        self.assertEqual([sheet[c].value for c in ('F3','G3','H3','H8','H9','H10','H11')],[7,6,1,1,8,2,0])
        self.assertNotIn('OT14',[c.value for c in sheet[1]]);self.assertIn('SUM(',sheet['H6'].value);book.close()
    def test_ski_unknown_is_balanced_across_middle_qualified_codes_and_audited(self):
        from collections import Counter
        confirmed = {'DR01':39, 'SM01':40, 'SM03':40, 'OT07':52, 'AU01':60, 'OT06':48}
        codes = [code for code, count in confirmed.items() for _ in range(count)]
        txt_lines, ski_lines = [], []
        for offset in range(0, len(codes), 5):
            strip = f'RX-{offset+1:04}'
            txt_lines.append(f'1,RX-,{strip}')
            for x, code in enumerate(codes[offset:offset+5], 1):
                txt_lines.append(f'RX-,{strip},{x},1,T,{code}')
                ski_lines.append(f'{strip},{x},1')
            txt_lines.append(f'RX-,{strip},EOS')
        txt_lines.extend(['1,RX-,RX-PENDING', 'RX-,RX-PENDING,EOS'])
        ski_lines.extend(f'RX-PENDING,{x},1' for x in range(1,6))
        self.txt.write_text('\n'.join(txt_lines))
        self.ski.write_text('\n'.join(ski_lines))
        summary=self.calculate();before=self.txt.read_bytes();output=self.root/'report.xlsx'
        self.assertFalse(unresolved_counts(summary));self.assertFalse(export_issues(summary))
        report=build_yield_report_aggregation(summary)
        automatic = report['automatic_allocation_groups']
        self.assertEqual({group['code'] for group in automatic}, {'SM03', 'OT07'})
        allocated = Counter({group['code']:len(group['coords']) for group in automatic})
        self.assertEqual(sorted(allocated.values()), [2,3])
        self.assertEqual(report['ng_units'], len(codes)+5)
        legacy = build_yield_report_aggregation(self.calculate(True))['automatic_allocation_groups']
        self.assertEqual({group['code']:group['coords'] for group in automatic},
                         {group['code']:group['coords'] for group in legacy})
        self.assertEqual(automatic, build_yield_report_aggregation(self.calculate())['automatic_allocation_groups'])
        self.assertEqual(before,self.txt.read_bytes())
        self.assertTrue(export_report_to_2xlsx(report,'',str(output))['success'])
        from openpyxl import load_workbook
        book=load_workbook(output)
        audit = [cells[:5] for cells in book['분류 이력'].iter_rows(min_row=2, values_only=True)]
        self.assertCountEqual(audit, [('RX-PENDING', x, y, 'UNKNOWN', group['code'])
                                     for group in automatic for x, y in group['coords']])
        book.close()
        target_code = automatic[0]['code']
        auto_row = next(row for row in trace_rows(summary['report_trace'], target_code)[0]
                        if row['reason'] == SKI_UNKNOWN_ALLOCATED)
        classify(summary, [auto_row], 'EN00', 'Verified actual code')
        self.assertEqual(summary['report_code_distribution']['EN00'], 1)
        self.assertEqual(summary['report_code_distribution'][target_code], confirmed[target_code]+allocated[target_code]-1)
        self.ski.write_text('\n'.join(line for line in ski_lines if line != 'RX-PENDING,5,1'))
        (self.bbt/'RX-PENDING.rpt').write_text('X:004 Y:001 = 1 - OT14\n')
        fresh = self.calculate()
        fresh_report = build_yield_report_aggregation(fresh)
        self.assertEqual({x for group in fresh_report['automatic_allocation_groups'] for x,_ in group['coords']}, {1,2,3})
        self.assertEqual(fresh_report['ot14_excluded_units'],1)
        self.assertEqual(fresh_report['ng_units'],len(codes)+3)

    def test_unknown_without_ski_is_not_automatically_classified(self):
        self.fixture(('UNKNOWN',))
        summary=calculate_yield_summary_by_process([self.txt],[],[],[],None,8,7)
        self.assertEqual(unresolved_counts(summary), {'UNKNOWN':1})
        self.assertTrue(export_issues(summary))

    def test_known_process_code_wins_over_unknown_at_the_same_ski_coordinate(self):
        self.fixture(('SM03',))
        ivs = self.root / 'ivs.txt'
        ivs.write_text(self.txt.read_text().replace('SM03', 'UNKNOWN'))
        summary = calculate_yield_summary_by_process([self.txt], [], [ivs], [], None, 8, 7, self.bbt, self.ski)
        self.assertEqual(summary['report_code_distribution'], {'SM03':1})
        self.assertEqual(build_yield_report_aggregation(summary)['automatic_allocation_groups'], [])
    def test_9999_is_exported_without_forced_classification(self):
        from openpyxl import load_workbook
        summary=self.fixture(('9999',));report=build_yield_report_aggregation(summary)
        self.assertFalse(unresolved_counts(summary));self.assertFalse(export_issues(summary))
        self.assertEqual(report['unresolved_codes'],{});self.assertEqual(report['ng_units'],1)
        output=self.root/'9999.xlsx';self.assertTrue(export_report_to_2xlsx(report,'',str(output))['success'])
        book=load_workbook(output);sheet=book.active
        self.assertEqual(sheet['J1'].value,'9999');self.assertEqual(sheet['J3'].value,1);book.close()
        # Optional reclassification remains available and survives recalculation.
        classify(summary,trace_rows(summary['report_trace'],'9999')[0],'SM03','Reviewed optional 9999')
        self.assertEqual(self.calculate()['report_code_distribution'],{'SM03':1})
    def test_9999_does_not_bypass_strip_mismatch_or_other_code_guards(self):
        summary=self.fixture(('9999',));summary['per_strip_rows'][0]['final_xout']+=1
        self.assertTrue(export_issues(summary))
        output=self.root/'blocked.xlsx';self.assertFalse(export_report_to_2xlsx(build_yield_report_aggregation(summary),'',str(output))['success']);self.assertFalse(output.exists())
        mixed=self.fixture(('9999','UNKNOWN','1111'));self.assertEqual(unresolved_counts(mixed),{'UNKNOWN':1})
        self.assertTrue(export_issues(mixed))
    def test_saved_review_reloads_only_for_identical_sources_and_can_reset(self):
        summary=self.fixture(('UNKNOWN',));classify(summary,trace_rows(summary['report_trace'],'UNKNOWN')[0],'OT12','Checked')
        loaded=self.calculate();self.assertFalse(unresolved_counts(loaded));self.assertEqual(loaded['report_code_distribution'],{'OT12':1})
        reset_classification(loaded);self.assertEqual(self.calculate()['report_code_distribution'],{'UNKNOWN':1})
        classify(summary,trace_rows(summary['_original_report_trace'],'UNKNOWN')[0],'SM03','Checked')
        self.txt.write_text(self.txt.read_text()+'\n')
        self.assertEqual(self.calculate()['report_code_distribution'],{'UNKNOWN':1})
        with self.assertRaises(ValueError):classify(summary,trace_rows(summary['_original_report_trace'],'UNKNOWN')[0],'SM03','Checked')
    def test_selected_coordinate_only_and_input_validation(self):
        summary=self.fixture(('UNKNOWN','UNKNOWN'));rows=trace_rows(summary['report_trace'],'UNKNOWN')[0]
        for target,note in (('9999','reason'),('UNKNOWN','reason'),('SM03','')):
            with self.assertRaises(ValueError):classify(summary,rows,target,note)
        classify(summary,rows[:1],'OT12','Checked first unit')
        self.assertEqual(summary['report_code_distribution'],{'UNKNOWN':1,'OT12':1})
    def test_mismatch_gate_uses_raw_table_counts_even_after_ot14_exclusion(self):
        summary=self.fixture(('SM03',),True)
        summary['per_strip_rows'][0]['final_xout']+=1
        self.assertEqual(len(mismatch_rows(summary)),1)
        output=self.root/'blocked.xlsx';self.assertFalse(export_report_to_2xlsx(build_yield_report_aggregation(summary),'',str(output))['success']);self.assertFalse(output.exists())
    def test_classifying_as_ot14_recomputes_metrics_once_and_resets(self):
        summary=self.fixture(('UNKNOWN',));classify(summary,trace_rows(summary['report_trace'],'UNKNOWN')[0],'OT14','Confirmed prior process')
        for _ in range(3):
            report=build_yield_report_aggregation(summary);self.assertEqual((report['total_units'],report['ng_units'],report['ot14_excluded_units']),(7,0,1))
        reset_classification(summary);self.assertEqual((summary['total_units'],summary['defective_units']),(8,1))
    def test_changed_source_blocks_a_prepared_report(self):
        summary=self.fixture(('SM03',));report=build_yield_report_aggregation(summary)
        self.ski.write_text(self.ski.read_text()+'RX-01,2,1\n')
        output=self.root/'blocked.xlsx';self.assertFalse(export_report_to_2xlsx(report,'',str(output))['success']);self.assertFalse(output.exists())

    def test_new_rpt_requires_recalculation_and_takes_precedence(self):
        summary=self.fixture(('SM03','OT07'))
        self.assertEqual(export_issues(summary),[])
        (self.bbt/'RX-01.rpt').write_text('X:002 Y:001 = 1 - OT14\n')
        self.assertTrue(export_issues(summary))
        output=self.root/'stale.xlsx'
        self.assertFalse(export_report_to_2xlsx(build_yield_report_aggregation(summary),'',str(output))['success'])
        self.assertEqual(self.calculate()['report_code_distribution'], {'SM03':1, 'OT14':1})

    def test_equal_counts_different_coordinates_block_and_notify_once_per_snapshot(self):
        self.fixture(('SM03','OT12'))
        self.ski.write_text('RX-01,1,1\nRX-01,3,1\n')
        with patch('core.collection.cache_root',return_value=self.root/'cache'):
            for legacy in (False,True):
                summary=self.calculate(legacy)
                row=summary['its_gathering_differences'][0]
                self.assertEqual((row['txt_only'],row['ski_only']), ([(2,1)],[(3,1)]))
                self.assertEqual(row['sources'], {'VRS':[str(self.txt)]})
                self.assertEqual(len(mismatch_rows(summary)),1)
                self.assertTrue(export_issues(summary))
                self.assertEqual(record_its_alert(summary,'MODEL'),not legacy)
            self.ski.write_text('RX-01,1,1\nRX-01,4,1\n\n')
            self.assertTrue(record_its_alert(self.calculate(),'MODEL'))
            self.ski.write_text('RX-01,1,1\nRX-01,2,1\n')
            self.assertFalse(record_its_alert(self.calculate(),'MODEL'))
    def test_all_ot14_has_zero_denominator_and_no_defect_columns(self):
        self.txt.write_text('1,RX-,RX-01\nRX-,RX-01,EOS\n')
        coords=''.join(f'X:{i:03d} Y:001 = 1 - OT14\n' for i in range(1,9))
        (self.bbt/'RX-01.rpt').write_text(coords)
        self.ski.write_text(''.join(f'RX-01,{i},1\n' for i in range(1,9)))
        summary=self.calculate();report=build_yield_report_aggregation(summary)
        self.assertEqual((report['total_units'],report['ng_units'],report['ok_units']),(0,0,0))
        self.assertEqual(summary['unit_yield_percent'],0);self.assertFalse(report['final_defects_by_code_including_bbt'])
        self.assertEqual(report['ot14_excluded_units'],8)
        self.assertTrue(export_report_to_2xlsx(report,'',str(self.root/'zero.xlsx'))['success'])
    def test_unrelated_strip_rpt_change_does_not_invalidate_review(self):
        other=self.bbt/'OTHER.rpt';other.write_text('X:001 Y:001 = 1 - OT14\n')
        summary=self.fixture(('SM03',),True)
        other.write_text('X:001 Y:001 = 1 - OT14\nX:002 Y:001 = 1 - OT14\n')
        self.assertFalse(export_issues(summary))
    def test_legacy_txt_also_traces_1111_as_ot06_and_excludes_ot14(self):
        summary=self.fixture(('1111',),True,legacy=True)
        self.assertEqual((summary['total_units'],summary['defective_units']),(7,1))
        self.assertFalse(unresolved_counts(summary));self.assertFalse(export_issues(summary))
        report=build_yield_report_aggregation(summary)
        self.assertEqual(report['final_defects_by_code_including_bbt']['OT06']['count'],1)
        self.assertEqual(trace_rows(summary['report_trace'],'OT06')[0][0]['code'],'1111')
