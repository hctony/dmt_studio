"""IVS population includes good strips; never infer total size from defects."""
import json
from config import APP_SETTINGS, YIELD_MODEL_SETTINGS_PATH
from core.yield_rates import production_units


def model_units(model):
    try:
        data = json.loads(YIELD_MODEL_SETTINGS_PATH.read_text(encoding='utf-8-sig'))
        value = next((p.get('units_per_strip') for name,p in data.items() if name.upper()==model.upper()), None)
        if value is not None and int(value)>0:
            return int(value)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        saved = int(APP_SETTINGS.value('yield/model_units/'+model, 0) or 0)
        if saved>0:
            return saved
        if str(APP_SETTINGS.value('gathering/model','')).upper()==model.upper():
            rows=int(APP_SETTINGS.value('gathering/x_count',0));cols=int(APP_SETTINGS.value('gathering/y_count',0))
            return rows*cols if rows>0 and cols>0 else 0
    except (ValueError, TypeError):
        pass
    return 0


def xml_grid_units(root):
    """Only explicit paired row/column metadata; not max observed defect coords."""
    rows={'rows','rowcount','unitrows','nrows'}
    columns={'columns','columncount','cols','unitcolumns','ncols'}
    values=set()
    for element in root.iter():
        metadata={key.lower().replace('_','').replace('-',''):value for key,value in element.attrib.items()}
        for child in element:
            key=child.tag.split('}')[-1].lower().replace('_','').replace('-','')
            if key in rows|columns and child.text:
                metadata[key]=child.text.strip()
        r=[metadata[key] for key in rows if key in metadata]
        c=[metadata[key] for key in columns if key in metadata]
        for row in r:
            for col in c:
                try:
                    if 0<int(row)<=1024 and 0<int(col)<=1024:
                        values.add(int(row)*int(col))
                except (ValueError,TypeError):
                    pass
    return next(iter(values)) if len(values)==1 else 0


def population_payload(strip_ids, configured_units, grid_units=(), missing=0, scope='lot', parse_errors=0):
    ids=sorted(set(strip_ids));grids=sorted(set(grid_units)-{0});units=int(configured_units or 0)
    conflict=len(grids)>1 or (units>0 and grids and grids!=[units])
    if units<=0 and len(grids)==1:
        units=grids[0]
    complete=bool(ids) and not missing and not parse_errors and not conflict
    return {'strip_ids':ids,'strip_count':len(ids),'units_per_strip':units,'nas_grid_units':grids,
            'missing_barcode_folders':missing,'parse_errors':parse_errors,'scope':scope,
            'complete':complete,'unit_conflict':bool(conflict),
            'total_units':production_units(len(ids),units) if complete and units>0 else None}
