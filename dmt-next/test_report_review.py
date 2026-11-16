import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core.yield_calc import calculate_yield_summary_by_process,calculate_yield_summary_many,build_yield_report_aggregation
from core.report_review import classify,reset_classification,unresolved_counts,export_issues,mismatch_rows
from core.report_trace import trace_rows
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
    def test_all_three_unresolved_codes_block_export_until_reviewed(self):
        summary=self.fixture();before=self.txt.read_bytes();output=self.root/'blocked.xlsx'
        self.assertEqual(unresolved_counts(summary),{'1111':1,'UNKNOWN':1,'9999':1})
        self.assertTrue(export_issues(summary));self.assertFalse(export_report_to_2xlsx(build_yield_report_aggregation(summary),'',str(output))['success']);self.assertFalse(output.exists())
        for code in ('1111','UNKNOWN','9999'):classify(summary,trace_rows(summary['report_trace'],code)[0],'SM03','Inspected XML and SKI')
        self.assertFalse(unresolved_counts(summary));self.assertFalse(export_issues(summary))
        report=build_yield_report_aggregation(summary);self.assertEqual(report['final_defects_by_code_including_bbt']['SM03']['count'],3)
        self.assertEqual(len(report['classifications']),3);self.assertEqual(before,self.txt.read_bytes())
        self.assertTrue(export_report_to_2xlsx(report,'',str(output))['success'])
        from openpyxl import load_workbook
        book=load_workbook(output);self.assertEqual(book['분류 이력'].max_row,4);book.close()
    def test_saved_review_reloads_only_for_identical_sources_and_can_reset(self):
        summary=self.fixture(('1111',));classify(summary,trace_rows(summary['report_trace'],'1111')[0],'OT12','Checked')
        loaded=self.calculate();self.assertFalse(unresolved_counts(loaded));self.assertEqual(loaded['report_code_distribution'],{'OT12':1})
        reset_classification(loaded);self.assertEqual(unresolved_counts(self.calculate()),{'1111':1})
        classify(summary,trace_rows(summary['_original_report_trace'],'1111')[0],'SM03','Checked')
        self.txt.write_text(self.txt.read_text()+'\n')
        self.assertEqual(unresolved_counts(self.calculate()),{'1111':1})
        with self.assertRaises(ValueError):classify(summary,trace_rows(summary['_original_report_trace'],'1111')[0],'SM03','Checked')
    def test_selected_coordinate_only_and_input_validation(self):
        summary=self.fixture(('1111','1111'));rows=trace_rows(summary['report_trace'],'1111')[0]
        for target,note in (('9999','reason'),('UNKNOWN','reason'),('SM03','')):
            with self.assertRaises(ValueError):classify(summary,rows,target,note)
        classify(summary,rows[:1],'OT12','Checked first unit')
        self.assertEqual(unresolved_counts(summary),{'1111':1});self.assertEqual(summary['report_code_distribution']['OT12'],1)
    def test_mismatch_gate_uses_raw_table_counts_even_after_ot14_exclusion(self):
        summary=self.fixture(('SM03',),True)
        summary['per_strip_rows'][0]['final_xout']+=1
        self.assertEqual(len(mismatch_rows(summary)),1)
        output=self.root/'blocked.xlsx';self.assertFalse(export_report_to_2xlsx(build_yield_report_aggregation(summary),'',str(output))['success']);self.assertFalse(output.exists())
    def test_classifying_as_ot14_recomputes_metrics_once_and_resets(self):
        summary=self.fixture(('1111',));classify(summary,trace_rows(summary['report_trace'],'1111')[0],'OT14','Confirmed prior process')
        for _ in range(3):
            report=build_yield_report_aggregation(summary);self.assertEqual((report['total_units'],report['ng_units'],report['ot14_excluded_units']),(7,0,1))
        reset_classification(summary);self.assertEqual((summary['total_units'],summary['defective_units']),(8,1))
    def test_changed_source_blocks_a_prepared_report(self):
        summary=self.fixture(('SM03',));report=build_yield_report_aggregation(summary)
        self.ski.write_text(self.ski.read_text()+'RX-01,2,1\n')
        output=self.root/'blocked.xlsx';self.assertFalse(export_report_to_2xlsx(report,'',str(output))['success']);self.assertFalse(output.exists())
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
    def test_legacy_txt_also_traces_codes_and_excludes_ot14(self):
        summary=self.fixture(('1111',),True,legacy=True)
        self.assertEqual((summary['total_units'],summary['defective_units']),(7,1));self.assertEqual(unresolved_counts(summary),{'1111':1})
        classify(summary,trace_rows(summary['report_trace'],'1111')[0],'SM03','Checked')
        self.assertFalse(unresolved_counts(summary));self.assertFalse(export_issues(summary))
