"""Locate and parse only the inspection XML needed by a report's image targets."""
from collections import defaultdict
from dataclasses import asdict

from core.cancellation import check_cancelled
from core.image_catalog import ImageCatalog
from core.ivs import find_ivs_files, parse_ivs_xml
from core.ivs_latest import epoch_key, select_latest
from core.nas import find_fc_file, folder_index, iter_dirs, list_lot_trials, load_valid_bundle_indexes
from core.rework_scope import cutoff
from core.xml_followup import image_records


def _barcode(sftp, path, metadata=None):
    try:
        return sftp.read_barcode_prefix(path, metadata)
    except FileNotFoundError:
        return None


def _file_metadata(sftp, paths, after):
    files = {}
    for path in paths:
        try:
            metadata = sftp.metadata(path)
        except FileNotFoundError:
            continue
        stamp = metadata.st_mtime
        if not after or (stamp is not None and stamp >= after):
            files[path] = metadata
    return files


def _folder_barcodes(sftp, context, folder, files):
    by_barcode = defaultdict(list)
    unknown = []
    for path, metadata in files.items():
        barcode = _barcode(sftp, path, metadata)
        if barcode:
            by_barcode[barcode].append(path)
        else:
            unknown.append(path)
    if unknown:
        fc = find_fc_file(f'{context.path}/{folder}', folder, sftp)
        barcode = _barcode(sftp, fc) if fc and fc not in unknown else None
        if barcode:
            by_barcode[barcode].extend(unknown)
        elif len(by_barcode) == 1:
            next(iter(by_barcode.values())).extend(unknown)
        else:
            return by_barcode, True
    return by_barcode, False


def lookup_images(sftp, model, lot, baseline, should_stop, progress):
    targets = set(baseline['image_assignments'])
    if not targets:
        return [], {'image_counts': {}, 'image_target_strips': 0}
    if any(not value or value in {'.', '..'} or '/' in value or '\\' in value for value in (model, lot)):
        raise ValueError('Invalid model or LOT')
    sftp.should_stop = should_stop
    sftp._dmt_lazy_images = True
    trials = list_lot_trials(model, lot, sftp, should_stop)
    after = cutoff(baseline['scope'])
    folders = []
    unidentified = 0
    for index, context in enumerate(trials):
        progress(index, len(trials), f'{lot} · {context.machine}/{context.trial}')
        valid = load_valid_bundle_indexes(context.path, sftp)
        if not valid:
            continue
        for folder in iter_dirs(sftp, context.path):
            check_cancelled(should_stop)
            if folder_index(folder) not in valid:
                continue
            root = f'{context.path}/{folder}'
            files = _file_metadata(sftp, find_ivs_files(root, sftp), after)
            if not files:
                if after:
                    continue
                fc = find_fc_file(root, folder, sftp)
                files = _file_metadata(sftp, [fc], after) if fc else {}
                if not files:
                    unidentified += 1
                    continue
            newest = max((meta.st_mtime for meta in files.values() if meta.st_mtime is not None),
                         default=float('-inf'))
            folders.append((newest, context, folder, files))

    events = []
    locations = defaultdict(list)
    latest_times = {}
    # Each group's maximum time bounds all its views. Once every target has a
    # time, groups strictly older than all targets cannot change their winner.
    for index, (newest, context, folder, files) in enumerate(sorted(folders, key=lambda row: row[0], reverse=True)):
        check_cancelled(should_stop)
        if len(latest_times) == len(targets) and newest < min(latest_times.values()):
            break
        progress(index, len(folders), f'{lot} · {context.machine}/{context.trial}/{folder}')
        by_barcode, missing = _folder_barcodes(sftp, context, folder, files)
        unidentified += missing
        for barcode in by_barcode.keys() & targets:
            for path in by_barcode[barcode]:
                stamp = files[path].st_mtime
                event = {'strip_id': barcode, 'machine': context.machine, 'trial': context.trial,
                         'strip_folder': folder, 'source_xml': path, 'source_mtime': stamp}
                events.append(event)
                if stamp is not None:
                    latest_times[barcode] = max(stamp, latest_times.get(barcode, stamp))
                if '_ivs.xml' in path.rsplit('/', 1)[-1].lower():
                    locations[barcode, epoch_key(event)].append((context, folder, path))
    _, selection = select_latest([], events)
    selected = selection['selected']
    # A file can contain split views for more than one target. Parse it just once.
    files = {(context.path, folder, path): context
             for strip, head in selected.items() for context, folder, path in locations[strip, epoch_key(head)]}
    records = []
    for index, ((_, folder, path), context) in enumerate(sorted(files.items())):
        progress(index + 1, len(files), f'{lot} · {context.machine}/{context.trial}/{folder}')
        for record in parse_ivs_xml(path, sftp, context, folder, should_stop=should_stop):
            head = selected.get(record.strip_id)
            if head and epoch_key(head) == (record.machine, record.trial, record.strip_folder):
                records.append(record)
    check_cancelled(should_stop)
    records = image_records(baseline, records)
    counts = dict.fromkeys(baseline['high'], 0)
    counts.update(ImageCatalog(records).counts())
    details = {
        'image_counts': counts,
        'image_target_strips': len(targets),
        'image_unmatched_strips': sorted(targets - {record.strip_id for record in records}),
        'image_unidentified_folders': unidentified,
        'scan_metrics': sftp.report(),
    }
    return [asdict(record) for record in records], details
