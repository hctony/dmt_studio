"""Persistent JSON-lines bridge. Reuses the original modules and their caches."""
from __future__ import annotations
import contextlib
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import gather, skipdata, yield_calc
from core.utils import infer_lot_id_from_values
from config import APP_SETTINGS

CACHES = {
    'gather_files': gather.GATHER_FILE_CACHE,
    'gather_directories': gather.GATHER_TXT_LIST_CACHE,
    'bbt_coordinates': skipdata.SKIPDATA_CACHE,
    'bbt_codes': skipdata.SKIPDATA_CODE_CACHE,
    'ski': skipdata.SKI_FILE_CACHE,
}

def plain(value):
    if dataclasses.is_dataclass(value): return plain(dataclasses.asdict(value))
    if isinstance(value, dict): return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)): return [plain(v) for v in value]
    if isinstance(value, Path): return str(value)
    return value

def dispatch(method, args):
    if method in ('ivs.scan', 'strip.scan', 'barcode.scan', 'trial.list', 'strip.search'):
        from workers.ivs_workers import IvsScanThread
        from workers.strip_workers import StripIdThread, FullXmlBarcodeScanThread, TrialListThread
        from workers.gather_workers import StripGatherSearchThread
        from PySide6.QtCore import Qt
        classes = {'ivs.scan': IvsScanThread, 'strip.scan': StripIdThread, 'barcode.scan': FullXmlBarcodeScanThread, 'trial.list': TrialListThread, 'strip.search': StripGatherSearchThread}
        worker = classes[method](**args)
        results, errors = [], []
        worker.finished.connect(lambda *values: results.append(values[0] if len(values) == 1 else values), Qt.DirectConnection)
        worker.failed.connect(errors.append, Qt.DirectConnection)
        worker.run()
        if errors: raise ValueError(errors[0])
        return results[0] if results else None
    if method == 'tests.run':
        from ui.tabs import test_tab
        results = []
        for group, label, func in test_tab.TESTS:
            try: getattr(test_tab, func)(); results.append({'group':group, 'test':label, 'passed':True})
            except Exception as exc: results.append({'group':group, 'test':label, 'passed':False, 'error':str(exc)})
        return results
    if method == 'yield.calculate':
        from core.ivs import load_ivs_dummy_scrap_strips
        paths = lambda key: [Path(p) for p in args.get(key, []) if p]
        optional = lambda key: Path(args[key]) if args.get(key) else None
        strip_json = optional('strip_json')
        lot = yield_calc.load_strip_id_json(strip_json)[0] if strip_json else ''
        dummy = load_ivs_dummy_scrap_strips(paths('ivs_json_paths'), lot)
        units, limit = int(args['units_per_strip']), int(args['xout_limit'])
        if units <= 0 or limit < 0: raise ValueError('Invalid units_per_strip or xout_limit')
        common = (strip_json, units, limit, optional('skipdata'), optional('ski'))
        if paths('vrs_paths') or paths('fvi_paths') or paths('ivs_paths'):
            return yield_calc.calculate_yield_summary_by_process(paths('vrs_paths'), paths('fvi_paths'), paths('ivs_paths'), paths('ivs_json_paths'), *common, args.get('filters'), dummy)
        if not paths('txt_paths'): raise ValueError('TXT input is required')
        return yield_calc.calculate_yield_summary_many(paths('txt_paths'), *common, dummy)
    if method == 'strip.validate':
        return yield_calc.analyze_ski_validation(Path(args['strip_json']), [Path(p) for p in args['txt_paths']], Path(args['skipdata']), Path(args['ski']))
    if method == 'inspection.stats':
        from datetime import datetime
        from core.inspection_stats import gather_inspection_stats
        return gather_inspection_stats(args['ivs_dirs'], args['vrs_dirs'], datetime.fromisoformat(args['start']), datetime.fromisoformat(args['end']))
    if method == 'lm.preview':
        from core.lm_data import preview_lm_data_cleanup
        return preview_lm_data_cleanup(Path(args['json_path']), Path(args['smk_folder']), Path(args['pmk_folder']))
    if method == 'cache.status': return {k: len(v) for k, v in CACHES.items()}
    if method == 'cache.clear':
        for cache in CACHES.values(): cache.clear()
        return dispatch('cache.status', {})
    if method == 'settings.read':
        # Only known UI namespaces; do not export unrelated registry entries.
        prefixes = ('ivs/', 'strip/', 'full_xml/', 'gathering/', 'gather_search/', 'inspection_stats/', 'yield/', 'diagnosis/', 'lm/')
        return {key: plain(APP_SETTINGS.value(key)) for key in APP_SETTINGS.allKeys() if key.startswith(prefixes)}
    if method == 'lot.infer': return infer_lot_id_from_values(*args['values'])
    if method == 'gather.parse': return gather.parse_gather_file(Path(args['path']), args['lot'])
    if method == 'gather.files': return gather.cached_txt_files(Path(args['path']))
    if method == 'bbt.load': return skipdata.load_skipdata_folder(Path(args['path']))
    if method == 'ski.load': return skipdata.load_ski_file(Path(args['path']))
    if method == 'barcode.compare':
        payload = json.loads(Path(args['xml_json']).read_text(encoding='utf-8-sig'))
        return yield_calc.compare_full_xml_barcode_with_strip_json(payload, Path(args['strip_json']))
    if method == 'strip.missing': return yield_calc.missing_strip_ids_from_files(Path(args['strip_json']), [Path(p) for p in args['txt_paths']])
    raise ValueError('Unknown method: ' + method)

def main():
    if '--legacy-ui' in sys.argv:
        from ui.main_window import MainWindow
        from PySide6.QtWidgets import QApplication
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                result = plain(dispatch(request['method'], request.get('args', {})))
            response = {'id': request['id'], 'result': result}
        except Exception as exc:
            response = {'id': request.get('id'), 'error': str(exc)}
        print(json.dumps(response, ensure_ascii=False), flush=True)

if __name__ == '__main__': main()
