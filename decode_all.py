import struct

lines = open('att_dump.txt', encoding='utf-16le').readlines()

# Limpiar BOM y extraer todos los datos
all_notifs = []
for l in lines:
    l = l.strip().lstrip('\ufeff')
    parts = l.split('\t')
    if len(parts) >= 5:
        try:
            frame = int(parts[0])
        except:
            continue
        handle = parts[3]
        val = parts[4]
        if handle in ('0x0014', '0x001a'):
            ch = 1 if handle == '0x0014' else 2
            all_notifs.append((frame, ch, val))

all_notifs.sort()

# Comandos unicos
print('=== NOTIFICACIONES UNICAS DEL RELOJ (watch -> phone) ===\n')
cmds_seen = set()
for frame, ch, val in all_notifs:
    cmd = val[:2].upper()
    key = cmd + str(ch)
    if key not in cmds_seen:
        cmds_seen.add(key)
        print(f'Ch{ch} [{cmd}]: {val[:80]}{"..." if len(val)>80 else ""}')

print()
# Decoders especificos
print('=== DECODE DE DATOS CLAVE ===\n')

for frame, ch, val in all_notifs:
    cmd = val[:2].upper()
    data = bytes.fromhex(val)
    payload = data[1:]

    if cmd == 'F7' and len(data) >= 8:
        year = struct.unpack_from('>H', data, 1)[0]
        month = data[3]
        day = data[4]
        page = data[5]
        bpms = list(data[6:])
        valid = [b for b in bpms if b > 30 and b < 220]
        if valid:
            print(f'F7 FC historial {year}-{month:02d}-{day:02d} pag{page}: {valid} bpm')

    elif cmd == 'B2' and len(data) >= 8:
        year = struct.unpack_from('>H', data, 1)[0]
        month = data[3]
        day = data[4]
        page = struct.unpack_from('>H', data, 5)[0]
        vals = list(data[7:])
        nonzero = [(i, v) for i, v in enumerate(vals) if v > 0]
        if nonzero:
            print(f'B2 Pasos {year}-{month:02d}-{day:02d} pag{page}: {nonzero}')

    elif cmd == 'CB':
        page = data[1] if len(data) > 1 else 0
        print(f'CB Sueno pag{page}: {val}')

    elif cmd == '34' and len(val) > 10:
        print(f'34 Historial wear: {val[:60]}')

# Extraer datos de sueno del canal 2
print('\n=== DATOS DE SUENO (channel 2, 0x0018 writes with CB) ===\n')
all_ch2 = []
for l in open('att_dump.txt', encoding='utf-16le').readlines():
    l = l.strip().lstrip('\ufeff')
    parts = l.split('\t')
    if len(parts) >= 5 and parts[3] == '0x0018':
        val = parts[4]
        if val.startswith('cb'):
            data = bytes.fromhex(val)
            page = data[1]
            count = data[2]
            print(f'CB pag{page} ({count} registros): ', end='')
            offset = 3
            records = []
            while offset + 3 < len(data):
                # Intentar diferentes formatos de registro de sueno
                b = data[offset:offset+4]
                records.append(b.hex())
                offset += 4
            print(' | '.join(records))
