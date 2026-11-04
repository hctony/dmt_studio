import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from core import report_templates
from core.export_excel import export_report_to_2xlsx


class ReportTemplateTests(unittest.TestCase):
    def test_bundled_template_is_cached_and_report_uses_original_style(self):
        from openpyxl import load_workbook
        source=Path(__file__).resolve().parent.parent/'resources/templates/report.xlsx'
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as folder, patch.object(report_templates,'DATA_DIR',Path(folder)), patch.object(report_templates,'APP_SETTINGS',MagicMock()):
            cached=report_templates.resolve_template()
            self.assertEqual(Path(cached).parent,Path(folder)/'templates')
            with patch.object(report_templates,'remember_template',side_effect=AssertionError('must reuse saved template')):
                self.assertEqual(report_templates.resolve_template(),cached)
            output=Path(folder)/'result.xlsx'
            report={'model_name':'MODEL','lot_id':'LOT-TEST','total_units':100,'ok_units':90,'ng_units':10,'ok_unit_aggregation':{'ok_unit_percent':90},'final_defects_by_code_including_bbt':{'OT12':{'count':7},'NEWCODE':{'count':3}}}
            result=export_report_to_2xlsx(report,cached,str(output))
            self.assertTrue(result['success'],result)
            self.assertTrue(result['used_template'])
            book=load_workbook(output);original=load_workbook(source)
            sheet=book.active
            self.assertEqual([sheet[c].value for c in ['D3','E3','F3','G3','H3','I3']],['MODEL','LOT-TEST',100,90,10,0.9])
            self.assertEqual(sheet['D3']._style,original.active['D3']._style)
            self.assertEqual(sheet.column_dimensions['A'].width,original.active.column_dimensions['A'].width)
            codes={sheet.cell(1,c).value:sheet.cell(3,c).value for c in range(10,sheet.max_column+1)}
            self.assertEqual(codes['OT12'],7);self.assertEqual(codes['NEWCODE'],3)
            self.assertEqual(sheet.cell(1,next(c for c in range(10,sheet.max_column+1) if sheet.cell(1,c).value=='NEWCODE'))._style,original.active['J1']._style)
            book.close();original.close()
            self.assertFalse(export_report_to_2xlsx(report,cached,cached)['success'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)

    def test_missing_explicit_template_does_not_silently_create_blank_report(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'report.xlsx'
            self.assertFalse(export_report_to_2xlsx({},str(Path(folder)/'missing.xlsx'),str(output))['success'])
            self.assertFalse(output.exists())
