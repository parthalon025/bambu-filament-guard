"""What the tests share: G-code, trays and statuses as the guard reads them, and fakes of the card and the printer."""
import math

import filament_guard as fg
import guard_ledger as gl


CFG = dict(fg.DEFAULTS)
MEASURED = dict(CFG, measured_reserve=True)                                             # the experimental measured reserve, turned on

def gcode(weights='9.99,10.87', used='1,3', types='PLA;PETG;PLA', colours='#0A2989;#FFFFFF;#C12E1F', ids='GFA00;GFG02;GFA00',
          nozzles='0.4', fmap='1,1,1', complete=True, body=('G1 X1',), settings=()):
    """A Bambu Studio G-code as 02.09.01.70 writes it, cut down to the lines that are read."""
    return '\n'.join([
        '; HEADER_BLOCK_START', '; BambuStudio 02.09.01.70', '; model printing time: 1h 2m 3s; total estimated time: 1h 9m 0s',
        f'; total filament weight [g] : {weights}', '; filament_density: 1.26,1.27,1.26', '; filament_diameter: 1.75,1.75,1.75'] + ([f'; filament: {used}'] if used else []) + [
        '; HEADER_BLOCK_END', '', '; CONFIG_BLOCK_START', f'; filament_colour = {colours}', f'; filament_ids = {ids}', f'; filament_map = {fmap}',
        '; filament_settings_id = "Bambu PLA Basic @BBL H2D";"Bambu PETG HF @BBL H2D 0.4 nozzle";"Bambu PLA Basic @BBL H2D"',
        f'; filament_type = {types}', f'; nozzle_diameter = {nozzles}', '; printer_model = Bambu Lab H2D'] + list(settings) + (['; CONFIG_BLOCK_END'] if complete else []) + list(body))

def tray(label, grams, colour='#C12E1F', typ='PLA', fid='GFA00', nozzle=None, name='PLA Basic', weight=None):
    return {'label': label, 'type': typ, 'fid': fid, 'colour': colour, 'name': name, 'grams': grams, 'weight': weight, 'nozzle': nozzle}

def fil(grams=50.0, colour='#C12E1F', typ='PLA', fid='GFA00', nozzle=None):
    return {'slot': 2, 'type': typ, 'fid': fid, 'colour': colour, 'name': 'Bambu PLA Basic', 'grams': grams, 'nozzle': nozzle}

H2D_JOB = {'gcode_state': 'RUNNING', 'subtask_name': 'Benchy_plate A', 'task_id': '11', 'total_layer_num': 240, 'mapping': [514, 257],
           'ams': {'ams': [{'id': '1', 'tray': [{'id': '1', 'tray_type': 'PLA', 'tray_color': 'C12E1FFF', 'remain_g': 81}]},
                           {'id': '2', 'tray': [{'id': '2', 'tray_type': 'PETG', 'tray_color': '101010FF', 'remain_g': -1, 'remain': -1, 'tray_weight': '0'}]}]}}

SLICE = {'printer_model': 'Bambu Lab H2D', 'time': '', 'layers': 240, 'filaments': [{'slot': 1, 'grams': 120.0}, {'slot': 2, 'grams': 30.0}]}

def c3():
    return next(t for t in gl.apply(fg.trays_of(H2D_JOB)[0], 'SERIAL') if t['label'] == 'C3')

K = math.pi * 0.875 ** 2 * 1.26 / 1000                                                  # grams in a mm of 1.75 mm filament at 1.26 g/cm3

def sliced(body, weights, layers=3, **more):
    """A whole G-code with two filaments, slots 1 and 3 (T0 and T2), of 1.26 g/cm3."""
    return gcode(weights=weights, body=body, **more).replace('; filament_density', f'; total layer number: {layers}\n; filament_density')

def e(grams):
    """The E of a move that extrudes this many grams (of slot 1 or 3)."""
    return f'{grams / K:.4f}'

def kept(layers):
    """A profile as layer_profile gives it, with nothing of it purge."""
    return {'layers': layers, 'purge': {slot: [0.0] * len(grams) for slot, grams in layers.items()}, 'pushed': {slot: [0.0] * len(grams) for slot, grams in layers.items()}}

def running(monkeypatch, status):
    """The printer the guard asks: SERIAL, an H2D, reporting status."""
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'access_code': {'SERIAL': 'code'}}); monkeypatch.setattr(fg, 'model_by_serial_prefix', lambda: {'SER': 'Bambu Lab H2D'})
    monkeypatch.setattr(fg, 'printer_ip', lambda *a, **k: 'printer'); monkeypatch.setattr(fg, 'read_status', lambda *a, **k: status)

BLUE = {'printer_model': 'Bambu Lab H2D', 'time': '', 'layers': 27,
        'filaments': [{'slot': 1, 'type': 'PLA', 'fid': 'GFA00', 'colour': '#0A2989', 'name': 'Bambu PLA Basic', 'grams': 85.3, 'nozzle': None}]}

BLUE_JOB = {'gcode_state': 'RUNNING', 'subtask_name': 'Benchy', 'layer_num': 1, 'total_layer_num': 27, 'mc_percent': 0, 'mapping': [514],
            'ams': {'ams': [{'id': '2', 'tray': [{'id': '2', 'tray_type': 'PLA', 'tray_color': '0A2989FF', 'tray_info_idx': 'GFA00', 'remain_g': 105, 'tray_weight': '1000'}]}]}}

TAG = 'A1B2C3D4E5F60718293A4B5C6D7E8F90'

def h2d(state, layer, c3, task='31'):
    """The H2D's status during a job of Benchy: slot 1 from C3 (AMS C, tray 3), which reads c3 g; None: C3 is empty."""
    tray = ({'id': '2', 'tray_type': 'PLA', 'tray_color': '0A2989FF', 'tray_info_idx': 'GFA00', 'remain_g': c3, 'remain': int(c3 / 10), 'tray_weight': '1000',
             'tray_uuid': TAG, 'tag_uid': '0000000000000000'} if c3 is not None else {'id': '2', 'state': 0})
    return {'gcode_state': state, 'subtask_name': 'Benchy', 'task_id': task, 'layer_num': layer, 'total_layer_num': 10, 'mapping': [514], 'ams': {'ams': [{'id': '2', 'tray': [tray]}]}}

def benchy():
    """The slice Benchy is printed from: 1 g in layer 0, 4 g in each of its 10 layers, all from C3."""
    fg.remember_slice('Benchy', 1, {'layers': 10, 'filaments': [{'slot': 1, 'grams': 41.0}]}, kept({1: [1.0] + [4.0] * 10}))

def run_job(rec, before=500.0, lost=2.0, per_layer=4.0, end='FINISH', fault=None, t=0.0, task='31'):
    """Feeds the recorder one print of Benchy, a look every 3 seconds: PREPARE (showing the job before's layer), RUNNING
    from layer 0 to 10, and the end state three times, 30 s apart. C3 reads `before`, `lost` less at layer 1, then
    per_layer less at each layer. fault(state, layer, status, t) -> (status, t) may change any look."""
    g, looks = before, [('PREPARE', 27, before), ('RUNNING', 0, before)]
    g -= lost
    for n in range(1, 11): looks.append(('RUNNING', n, g)); g -= per_layer
    looks += [(end, 10, g)] * 3
    for state, layer, reading in looks:
        t += 30 if state == end else 3
        status = h2d(state, layer, reading, task)
        if fault: status, t = fault(state, layer, status, t)
        rec.look(status, t)
    return t

def blue_reads(g):
    return dict(BLUE_JOB, gcode_state='IDLE', ams={'ams': [{'id': '2', 'tray': [{'id': '2', 'tray_type': 'PLA', 'tray_color': '0A2989FF', 'tray_info_idx': 'GFA00', 'remain_g': g, 'tray_weight': '1000'}]}]})

def spool(tray, grams=None, typ='PLA', colour='101010', fid=''):
    """One tray of AMS A as the printer reports it (0 is A1): with grams, a 1 kg spool it measures; without, a spool it
    cannot measure."""
    amount = {'remain_g': grams, 'remain': int(grams / 10), 'tray_weight': '1000'} if grams is not None else {'remain_g': -1, 'remain': -1, 'tray_weight': '0'}
    return dict({'id': str(tray), 'tray_type': typ, 'tray_color': colour + 'FF', 'tray_info_idx': fid}, **amount)

def printing(state, spools, name='Part', task='2', mapping=(0,), layer=0, layers=100, **more):
    """The printer's status with a job on it that draws on AMS A, whose trays are `spools`; mapping: the tray of each of
    the job's filaments."""
    return dict({'gcode_state': state, 'subtask_name': name, 'task_id': task, 'layer_num': layer, 'total_layer_num': layers, 'mapping': list(mapping),
                 'ams': {'ams': [{'id': '0', 'tray': spools}]}}, **more)

def part(grams=300.0, title='Part', layers=100):
    """The slice a job named Part is printed from: one filament, the same grams in each of its 100 layers."""
    fg.remember_slice(title, 1, {'layers': layers, 'filaments': [{'slot': 1, 'grams': grams}]}, kept({1: [0.0] + [grams / layers] * layers}))

def needing(grams, typ='PLA', colour='#101010'):
    """A plate for the H2D that needs this much of one filament."""
    return {'printer_model': 'Bambu Lab H2D', 'time': '', 'layers': 50, 'filaments': [{'slot': 1, 'type': typ, 'fid': '', 'colour': colour, 'name': typ, 'grams': float(grams), 'nozzle': None}]}
