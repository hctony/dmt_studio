import io
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from dataclasses import replace
from core.nas_cache import ScanSftp,XmlStore
from core.nas import read_barcode_from_xml
from core.ivs import parse_ivs_xml,build_summary
from core.models import TrialContext,DefectRecord
from core.image_catalog import ImageCatalog


class LocalSftp:
    def __init__(self,root,latency=0):self.root=Path(root);self.reads=0;self.lists=0;self.stats=0;self.latency=latency
    def local(self,path):return self.root/str(path).lstrip('/')
    def open(self,path,mode='rb'):
        self.reads+=1;time.sleep(self.latency);return self.local(path).open(mode)
    def listdir_attr(self,path):
        self.lists+=1;time.sleep(self.latency)
        return [SimpleNamespace(filename=p.name,st_mode=p.stat().st_mode,st_mtime=p.stat().st_mtime,st_size=p.stat().st_size) for p in self.local(path).iterdir()]
    def stat(self,path):self.stats+=1;time.sleep(self.latency);return self.local(path).stat()
    def close(self):pass


class NasPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.file=self.root/'trial/1/one_ivs.xml';self.file.parent.mkdir(parents=True)
        self.file.write_text('<Root barcode="RX-01" rows="2" columns="2"><Unit>'+''.join(f'<P{i} unit_idx="{i}" ivs_1="SM03"/>' for i in range(4))+'</Unit></Root>')
        os.utime(self.file,(1000000000,1000000000));self.ctx=TrialContext('A','M','RX-','T','/trial')
    def session(self,namespace='test',latency=0):
        raw=LocalSftp(self.root,latency);s=ScanSftp(raw,XmlStore(namespace,self.root/'cache'));self.addCleanup(s.close);return s
    def test_scan_uses_xml_metadata_but_performs_zero_image_file_probes(self):
        s=self.session();s._dmt_lazy_images=True
        records=parse_ivs_xml('/trial/1/one_ivs.xml',s,self.ctx,'1')
        self.assertEqual(len(records),4);self.assertTrue(all(len(r.images)==3 for r in records))
        self.assertEqual(s.raw.reads,1);self.assertEqual(s.raw.stats,2);self.assertEqual(s.raw.lists,0)
    def test_xml_cache_survives_new_connection_and_keeps_same_summary(self):
        first=self.session();first._dmt_lazy_images=True;records=parse_ivs_xml('/trial/1/one_ivs.xml',first,self.ctx,'1');first.close()
        second=self.session();second._dmt_lazy_images=True;again=parse_ivs_xml('/trial/1/one_ivs.xml',second,self.ctx,'1')
        self.assertEqual(build_summary(records,4),build_summary(again,4));self.assertEqual(second.raw.reads,0)
        self.assertEqual(second.metrics['xml_cache_hits'],1)
        self.assertEqual(read_barcode_from_xml('/trial/1/one_ivs.xml',second),'RX-01')
    def test_changed_deleted_and_force_read_never_use_old_xml_body(self):
        first=self.session();self.assertEqual(read_barcode_from_xml('/trial/1/one_ivs.xml',first),'RX-01');first.close()
        self.file.write_text('<Root barcode="RX-02"/>');os.utime(self.file,(1000000100,1000000100))
        second=self.session();self.assertEqual(read_barcode_from_xml('/trial/1/one_ivs.xml',second),'RX-02');self.assertEqual(second.raw.reads,1);second.close()
        forced=self.session();forced.bypass_xml_cache=True
        self.assertEqual(read_barcode_from_xml('/trial/1/one_ivs.xml',forced),'RX-02');self.assertEqual(forced.raw.reads,1);forced.close()
        self.file.unlink();deleted=self.session();self.assertIsNone(read_barcode_from_xml('/trial/1/one_ivs.xml',deleted))
    def test_cache_is_isolated_by_nas_identity_and_recovers_from_corrupt_database(self):
        first=self.session('one');read_barcode_from_xml('/trial/1/one_ivs.xml',first);first.close()
        other=self.session('two');read_barcode_from_xml('/trial/1/one_ivs.xml',other);self.assertEqual(other.raw.reads,1);other.close()
        # A separate broken store must fall back to a direct NAS read.
        bad=self.root/'broken';bad.mkdir();(bad/'nas-xml.sqlite').write_bytes(b'not a sqlite database')
        s=ScanSftp(LocalSftp(self.root),XmlStore('one',bad));self.addCleanup(s.close)
        self.assertEqual(read_barcode_from_xml('/trial/1/one_ivs.xml',s),'RX-01')
    def test_images_resolve_actual_extension_only_when_requested_with_one_folder_listing(self):
        folder=self.root/'trial/1/Unit/U1/FC';folder.mkdir(parents=True)
        for suffix in ('_Sub2','','_Sub1'):(folder/('p0'+suffix+'.jpg')).write_bytes(b'image')
        s=self.session()
        for suffix in ('_Sub2','','_Sub1'):
            self.assertTrue(s.resolve_image('/trial/1/Unit/U1/FC/p0'+suffix+'.bmp').endswith('.jpg'))
        self.assertEqual(s.raw.lists,1);self.assertEqual(s.raw.stats,0)
    def test_mid_read_file_change_is_not_persisted(self):
        s=self.session();original=s.raw.open
        def opened(path,mode):
            file=original(path,mode);os.utime(self.file,(1000000200,1000000200));return file
        s.raw.open=opened;s.read_xml('/trial/1/one_ivs.xml');s.close()
        again=self.session();again.read_xml('/trial/1/one_ivs.xml');self.assertEqual(again.raw.reads,1)
    def test_cache_cannot_bypass_cancellation_or_cache_currently_written_xml(self):
        from core.cancellation import OperationCancelled
        first=self.session();first.read_xml('/trial/1/one_ivs.xml');first.close()
        stopped=self.session();stopped.should_stop=lambda:True
        with self.assertRaises(OperationCancelled):stopped.read_xml('/trial/1/one_ivs.xml')
        os.utime(self.file,None);live=self.session();live.read_xml('/trial/1/one_ivs.xml');live.close()
        again=self.session();again.read_xml('/trial/1/one_ivs.xml');self.assertEqual(again.raw.reads,1)

    def test_real_worker_counts_all_units_and_exports_cold_and_warm_scan_metrics(self):
        from workers.ivs_workers import IvsScanThread
        payloads=[]
        for run in range(2):
            s=self.session();worker=IvsScanThread('M','RX-',units_per_strip=4);results=[];errors=[]
            worker.finished.connect(results.append);worker.failed.connect(errors.append)
            with patch('workers.ivs_workers.connect',return_value=(SimpleNamespace(close=lambda:None),s)),patch('workers.ivs_workers.list_lot_trials',return_value=[self.ctx]),patch('workers.ivs_workers.load_valid_bundle_indexes',return_value={1}):worker.run()
            self.assertEqual(errors,[]);payloads.append(results[0])
        self.assertEqual(payloads[0]['summary'],payloads[1]['summary'])
        self.assertEqual(payloads[1]['population']['total_units'],4)
        self.assertEqual(payloads[0]['scan_metrics']['xml_reads'],1)
        self.assertEqual(payloads[1]['scan_metrics']['xml_reads'],0)
        self.assertEqual(payloads[1]['scan_metrics']['xml_cache_hits'],1)

    def test_large_xml_prefetch_has_a_bounded_request_count(self):
        self.file.write_bytes(b'<Root>'+b' '*300000+b'</Root>');os.utime(self.file,(1000000000,1000000000))
        s=self.session();calls=[]
        class File(io.BytesIO):
            def prefetch(self,**kwargs):calls.append(kwargs)
        s.raw.open=lambda *args:File(self.file.read_bytes())
        self.assertEqual(len(s.read_xml('/trial/1/one_ivs.xml')),self.file.stat().st_size)
        self.assertEqual(calls,[{'file_size':self.file.stat().st_size,'max_concurrent_requests':4}])

    def test_four_separate_connections_share_cache_without_sharing_sftp_or_sqlite_handles(self):
        from concurrent.futures import ThreadPoolExecutor
        def read_one(_):
            s=ScanSftp(LocalSftp(self.root),XmlStore('parallel',self.root/'parallel-cache'))
            try:return read_barcode_from_xml('/trial/1/one_ivs.xml',s)
            finally:s.close()
        with ThreadPoolExecutor(max_workers=4) as pool:self.assertEqual(list(pool.map(read_one,range(4))),['RX-01']*4)
        warm=ScanSftp(LocalSftp(self.root),XmlStore('parallel',self.root/'parallel-cache'));self.addCleanup(warm.close)
        self.assertEqual(read_barcode_from_xml('/trial/1/one_ivs.xml',warm),'RX-01');self.assertEqual(warm.raw.reads,0)

    def test_image_lookup_reads_only_latest_target_xml_in_full(self):
        from core.xml_image_lookup import lookup_images

        latest = TrialContext('B', 'M', 'RX-', '6', '/latest')
        for name, barcode, stamp, idx, code in (
            ('3/front_ivs.xml', 'RX-01', 1000000040, 0, '9998'),
            ('3/back_ivs.xml', 'RX-01', 1000000020, 1, 'OT06'),
            ('4/unrelated_ivs.xml', 'RX-02', 1000000050, 0, 'SM03'),
        ):
            file = self.root / 'latest' / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(f'<Root barcode="{barcode}"><Unit><P0 unit_idx="{idx}" ivs_1="{code}"/>'
                            '<P1 unit_idx="3" ivs_1="SM03"/></Unit>' + ' ' * 20000 + '</Root>')
            os.utime(file, (stamp, stamp))
        baseline = {'scope': {}, 'high': ['OT06'], 'evidence': {'RX-01': {'width': 4}},
                    'image_assignments': {'RX-01': [[1, 1, 'OT06'], [2, 1, 'OT06']]}}
        with patch('core.xml_image_lookup.list_lot_trials', return_value=[self.ctx, latest]), patch(
                'core.xml_image_lookup.load_valid_bundle_indexes', return_value={1, 3, 4}):
            cold = self.session()
            with patch.object(cold.raw, 'open', wraps=cold.raw.open) as opened:
                records, details = lookup_images(cold, 'M', 'RX-', baseline, lambda: False, lambda *_: None)
            self.assertEqual({record['source_xml'] for record in records},
                             {'/latest/3/front_ivs.xml', '/latest/3/back_ivs.xml'})
            self.assertEqual([record['ivs_1'] for record in records], ['OT06', 'OT06'])
            self.assertEqual(details['image_counts'], {'OT06': 2})
            self.assertEqual(details['scan_metrics']['xml_reads'], 2)
            self.assertEqual(details['scan_metrics']['barcode_bytes'], 3 * 16 * 1024)
            self.assertEqual({call.args[0] for call in opened.call_args_list},
                             {'/latest/3/front_ivs.xml', '/latest/3/back_ivs.xml', '/latest/4/unrelated_ivs.xml'})
            cold.close()

            warm = self.session()
            again, details = lookup_images(warm, 'M', 'RX-', baseline, lambda: False, lambda *_: None)
            self.assertEqual(again, records)
            self.assertEqual(warm.raw.reads, 0)
            self.assertEqual(details['scan_metrics']['barcode_cache_hits'], 3)

    def test_newer_fc_only_strip_does_not_use_old_defect_images(self):
        from core.xml_image_lookup import lookup_images

        file = self.root / 'trial/2/2_fc.xml'
        file.parent.mkdir()
        file.write_text('<Root barcode="RX-01"/>', encoding='utf-16')
        os.utime(file, (1000000040, 1000000040))
        baseline = {'scope': {}, 'high': ['SM03'], 'evidence': {'RX-01': {'width': 4}},
                    'image_assignments': {'RX-01': [[1, 1, 'SM03']]}}
        sftp = self.session()
        with patch('core.xml_image_lookup.list_lot_trials', return_value=[self.ctx]), patch(
                'core.xml_image_lookup.load_valid_bundle_indexes', return_value={1, 2}):
            records, details = lookup_images(sftp, 'M', 'RX-', baseline, lambda: False, lambda *_: None)
        self.assertEqual(records, [])
        self.assertEqual(details['image_unmatched_strips'], ['RX-01'])
        self.assertEqual(details['scan_metrics']['xml_reads'], 0)
        self.assertEqual(details['scan_metrics']['barcode_reads'], 1)

    def test_top_image_download_saves_one_png_per_complete_set_and_stops_at_quantity(self):
        from PySide6.QtGui import QColor, QImage
        from PySide6.QtWidgets import QApplication
        from workers.ivs_workers import TopImageDownloadThread

        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        base = DefectRecord('RX-01', 'A', 'M', 'RX-', 'T', '1', 'Unit', 'FC', '0', '0',
                            'SM03', 'SM03', '', 'F', 0, 0, 0, 0, 'data.xml', [])
        candidates = []
        skipped = self.root / 'lighting'
        skipped.mkdir()
        colors = ['red', 'green', 'blue']
        for index in range(4):
            images = [f'/lighting/p{index}{suffix}.bmp' for suffix in ('_Sub2', '', '_Sub1')]
            candidates.append(replace(base, images=images, img_merge_idx=str(index)))
            for path, color in zip(images[:2] if index == 0 else images, colors):
                image = QImage(128, 128, QImage.Format_RGB32)
                image.fill(QColor(color))
                self.assertTrue(image.save(str(self.root / path.lstrip('/'))))
        sftp = self.session()
        worker = TopImageDownloadThread({'SM03': candidates, 'OT06': [candidates[0]]},
                                        {'SM03': 2, 'OT06': 1}, self.root / 'export')
        counts, errors = [], []
        worker.finished.connect(counts.append)
        worker.failed.connect(errors.append)
        with patch('workers.ivs_workers.connect', return_value=(SimpleNamespace(close=lambda: None), sftp)):
            worker.run()
        self.assertEqual(errors, [])
        self.assertEqual(counts, [{'SM03': 2, 'OT06': 0}])
        self.assertEqual(sftp.raw.reads, 6)
        outputs = sorted((self.root / 'export').rglob('*.png'))
        self.assertEqual(len(outputs), 2)
        for path in outputs:
            combined = QImage(str(path))
            self.assertEqual((combined.width(), combined.height()), (384, 172))
            self.assertEqual([combined.pixelColor(x, 169) for x in (125, 253, 381)],
                             [QColor(color) for color in colors])


class ImageCatalogTests(unittest.TestCase):
    def records(self):
        base=DefectRecord('RX-01','A','M','RX-','T','1','Unit','FC','0','0','SM03','SM03','','F',1,1,0,0,'xml',[])
        return [replace(base,strip_id=f'RX-{i:03d}',unit_idx=str(i),ivs_1=code,images=[f'/{code}/{i}/p0{suffix}.bmp' for suffix in ('_Sub2','','_Sub1')]) for code in ('SM03','OT12') for i in range(30)]
    def test_first_ten_per_code_do_not_reduce_counts_or_download_range(self):
        records=self.records();before=build_summary(records,120);catalog=ImageCatalog(records)
        self.assertEqual(catalog.counts(),{'OT12':30,'SM03':30});self.assertEqual(len(catalog.visible(records)),20)
        self.assertEqual(catalog.candidate_files(),180);self.assertEqual(build_summary(records,120),before)
        catalog.add_view_range('SM03',11,20)
        self.assertEqual(len(catalog.visible([r for r in records if r.ivs_1=='SM03'])),20)
        selected=catalog.download_records('SM03',21,30)
        self.assertEqual([r.unit_idx for r in selected],[str(i) for i in range(20,30)])
    def test_duplicates_and_invalid_ranges_never_duplicate_download_or_fetch_all(self):
        records=self.records();records.append(replace(records[0],source_xml='duplicate.xml'))
        catalog=ImageCatalog(records);self.assertEqual(catalog.counts()['SM03'],30)
        self.assertEqual(len(catalog.download_records('SM03',1,1)),1)
        for start,end in ((0,3),(2,1),(1,31)):
            with self.assertRaises(ValueError):catalog.select_range('SM03',start,end)
