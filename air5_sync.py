"""
Air5 Smartwatch - Sincronizador completo
Extrae: FC historica, SpO2, pasos, bateria, info del dispositivo
Guarda en JSON y CSV
"""
import asyncio
import struct
import json
import csv
import os
from datetime import datetime, date

from winrt.windows.devices.bluetooth import BluetoothLEDevice, BluetoothConnectionStatus
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattCommunicationStatus, GattWriteOption,
    GattClientCharacteristicConfigurationDescriptorValue, GattSession,
)
from winrt.windows.storage.streams import DataWriter, DataReader

# ── Configuracion ──────────────────────────────────────────────
MAC_ADDR   = "81:0A:B7:00:1D:BC"
OUTPUT_DIR = r"C:\Users\--X\Music\bluetooth\sync_data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Helpers ────────────────────────────────────────────────────
def mac_to_int(mac): return int(mac.replace(":", ""), 16)

def ts(): return datetime.now().strftime("%H:%M:%S")

def log(msg): print(f"[{ts()}] {msg}")

# ── Datos recolectados ─────────────────────────────────────────
collected = {
    "sync_time":  datetime.now().isoformat(),
    "device":     {},
    "battery_pct": None,
    "live_hr_bpm": None,
    "hr_history":  [],   # {datetime, bpm}
    "spo2_history":[],   # {datetime, pct}
    "steps_history":[],  # {datetime, count}
    "steps_today": None,
    "calories_today": None,
    "distance_today_m": None,
    "active_min_today": None,
}

# ── Decoders ───────────────────────────────────────────────────
def decode_f7(data: bytes):
    """FC historica: f7 [year_2b] [month] [day] [page] [12xbpm]"""
    if len(data) < 7: return
    year  = struct.unpack_from(">H", data, 1)[0]
    month = data[3]; day = data[4]; page = data[5]
    bpms  = list(data[6:])
    # page 0=00h, page 2=02h, page 4=04h... cada pagina = 1 hora
    hour_start = page  # pagina = hora del dia
    for i, bpm in enumerate(bpms):
        if 30 < bpm < 220:
            minute = i * 5
            total_min = hour_start * 60 + minute
            if total_min >= 24 * 60: continue
            h = total_min // 60; m = total_min % 60
            try:
                dt = datetime(year, month, day, h, m)
                collected["hr_history"].append({
                    "datetime": dt.isoformat(),
                    "bpm": bpm
                })
            except: pass

def decode_34(data: bytes):
    """SpO2 historial: 34 fa [year_2b] [month] [day] [hour_2b] [14 bytes] [spo2]"""
    if len(data) < 20: return
    if data[1] != 0xfa: return
    year  = struct.unpack_from(">H", data, 2)[0]
    month = data[4]; day = data[5]
    hour  = data[6]
    spo2  = data[-1]
    if 70 <= spo2 <= 100 and spo2 != 0xff:
        try:
            dt = datetime(year, month, day, hour, 0)
            collected["spo2_history"].append({
                "datetime": dt.isoformat(),
                "spo2_pct": spo2
            })
        except: pass

def decode_b2(data: bytes):
    """Pasos por slot: b2 [year_2b] [month] [day] [page_2b] [bytes]"""
    if len(data) < 9: return
    year  = struct.unpack_from(">H", data, 1)[0]
    month = data[3]; day = data[4]
    page  = struct.unpack_from(">H", data, 5)[0]
    vals  = list(data[7:])
    hour_start = page
    for i, v in enumerate(vals):
        if v > 0:
            minute = i * 5
            total_min = hour_start * 60 + minute
            if total_min >= 24 * 60: continue
            h = total_min // 60; m = total_min % 60
            try:
                dt = datetime(year, month, day, h, m)
                collected["steps_history"].append({
                    "datetime": dt.isoformat(),
                    "steps": v * 100  # aproximacion
                })
            except: pass

def decode_26(data: bytes):
    """Actividad diaria: 26 01 00 [pasos_2b] [cal_2b] [dist_2b] [min_activo]"""
    if len(data) < 9: return
    steps    = struct.unpack_from("<H", data, 3)[0]
    calories = struct.unpack_from("<H", data, 5)[0]
    dist_m   = struct.unpack_from("<H", data, 7)[0]
    active   = data[9] if len(data) > 9 else 0
    collected["steps_today"]     = steps
    collected["calories_today"]  = calories
    collected["distance_today_m"]= dist_m
    collected["active_min_today"]= active
    log(f"  Actividad: {steps} pasos | {calories} kcal | {dist_m}m | {active} min activos")

def decode_a2(data: bytes):
    pct = data[1] if len(data) > 1 else 0
    collected["battery_pct"] = pct
    log(f"  Bateria: {pct}%")

def decode_a1(data: bytes):
    try:
        serial = data[1:].decode("ascii").rstrip("\x00")
        collected["device"]["serial"] = serial
        log(f"  Serie: {serial}")
    except: pass

def decode_e5(data: bytes):
    if len(data) >= 4:
        sub = data[1]
        val = data[3]
        if sub == 0x11 and 30 < val < 220:
            collected["live_hr_bpm"] = val
            log(f"  FC en vivo: {val} bpm")

def decode_38(data: bytes):
    """Device info del canal 2"""
    try:
        name_bytes = data[2:16]
        name = name_bytes.decode("ascii").rstrip("\x00")
        mac_bytes  = data[22:28]
        mac = ":".join(f"{b:02X}" for b in mac_bytes)
        fw = f"{data[28]}.{data[29]}.{data[30]}" if len(data) > 30 else "?"
        collected["device"].update({"name": name, "mac": mac, "firmware": fw})
        log(f"  Dispositivo: {name} | MAC: {mac} | FW: {fw}")
    except: pass

# ── Conexion y sync ────────────────────────────────────────────
async def sync():
    log(f"Conectando a {MAC_ADDR}...")
    device = await BluetoothLEDevice.from_bluetooth_address_async(mac_to_int(MAC_ADDR))
    if not device:
        log("ERROR: Dispositivo no encontrado."); return False

    session = await GattSession.from_device_id_async(device.bluetooth_device_id)
    session.maintain_connection = True

    log("Esperando conexion BLE (max 60s)...")
    for i in range(60):
        if device.connection_status == BluetoothConnectionStatus.CONNECTED:
            log(f"Conectado en {i+1}s!"); break
        await asyncio.sleep(1)
    else:
        log("ERROR: No conecta."); session.close(); device.close(); return False

    # Obtener caracteristicas
    sr = await device.get_gatt_services_async()
    write1 = notify1 = write2 = notify2 = None
    for svc in sr.services:
        cr = await svc.get_characteristics_async()
        if cr.status != GattCommunicationStatus.SUCCESS: continue
        for ch in cr.characteristics:
            h = ch.attribute_handle
            if h == 0x0011: write1 = ch
            if h == 0x0013: notify1 = ch
            if h == 0x0017: write2 = ch
            if h == 0x0019: notify2 = ch

    pending_cmds = set()

    def on_notify(channel):
        def handler(sender, args):
            reader = DataReader.from_buffer(args.characteristic_value)
            data = bytes([reader.read_byte() for _ in range(reader.unconsumed_buffer_length)])
            if not data: return
            cmd = data[0]
            if   cmd == 0xA1: decode_a1(data)
            elif cmd == 0xA2: decode_a2(data)
            elif cmd == 0xE5: decode_e5(data)
            elif cmd == 0x26: decode_26(data); pending_cmds.discard("26")
            elif cmd == 0xF7: decode_f7(data); pending_cmds.discard("f7")
            elif cmd == 0x34: decode_34(data)
            elif cmd == 0xB2: decode_b2(data)
            elif cmd == 0x38: decode_38(data)
            elif cmd == 0xBB:
                log(f"  Estado: {'OK' if (len(data)>1 and data[1]==1) else 'ERR'}")
        return handler

    tok1 = tok2 = None
    if notify1:
        r = await notify1.write_client_characteristic_configuration_descriptor_async(
            GattClientCharacteristicConfigurationDescriptorValue.NOTIFY)
        if r == GattCommunicationStatus.SUCCESS:
            tok1 = notify1.add_value_changed(on_notify(1))
            log("Notificaciones Ch1 activas")
    if notify2:
        r = await notify2.write_client_characteristic_configuration_descriptor_async(
            GattClientCharacteristicConfigurationDescriptorValue.NOTIFY)
        if r == GattCommunicationStatus.SUCCESS:
            tok2 = notify2.add_value_changed(on_notify(2))
            log("Notificaciones Ch2 activas")

    async def send(hex_data, channel=1, label=""):
        w = write1 if channel == 1 else write2
        if not w: return
        if label: log(f">> {label}")
        writer = DataWriter()
        writer.write_bytes(bytearray.fromhex(hex_data))
        buf = writer.detach_buffer()
        try: await w.write_value_with_result_async(buf)
        except TypeError:
            await w.write_value_async(buf, GattWriteOption.WRITE_WITHOUT_RESPONSE)

    # ── Secuencia de inicializacion ──────────────────────────
    log("\n=== INICIANDO SINCRONIZACION ===\n")

    await send("0808442a01243943756ffffed921005f784be1dc", label="Handshake")
    await asyncio.sleep(0.5)

    await send("00f4000000000000000000000000000000000402", channel=2, label="Ch2 init")
    await asyncio.sleep(0.5)

    # Hora actual
    now = datetime.now()
    time_hex = f"a3{now.year:04x}{now.month:02x}{now.day:02x}{now.hour:02x}{now.minute:02x}{now.second:02x}"
    await send(time_hex, label="Sincronizar hora")
    await asyncio.sleep(1)

    # ── Datos basicos ────────────────────────────────────────
    await send("a1", label="Serial")
    await asyncio.sleep(1)
    await send("a2", label="Bateria")
    await asyncio.sleep(1)
    await send("bb", label="Estado")
    await asyncio.sleep(1)

    # ── Desactivar sedentario agresivo ───────────────────────
    # D1 FF 64: cada 255 min, umbral 100 pasos (practicamente silenciado)
    await send("d1ff64", label="Sedentario cada 255min (silenciado)")
    await asyncio.sleep(0.5)
    # D7: activar solo de 22hs a 23hs (1 hora al dia = raro que dispare)
    await send("d7160017000000", label="Ventana sedentario 22-23hs")
    await asyncio.sleep(0.5)

    # ── Actividad de hoy ─────────────────────────────────────
    await send("2601", label="Actividad del dia")
    await asyncio.sleep(2)

    # ── Historial FC (ultimos 7 dias) ────────────────────────
    log(">> Historial FC (7 dias)...")
    pending_cmds.add("f7")
    # Desde hace 7 dias
    from datetime import timedelta
    week_ago = now - timedelta(days=7)
    hist_hex = f"f7fa{week_ago.year:04x}{week_ago.month:02x}{week_ago.day:02x}{week_ago.hour:02x}{week_ago.minute:02x}"
    await send(hist_hex)
    await asyncio.sleep(8)  # Esperar todas las paginas

    # ── SpO2 historico ───────────────────────────────────────
    log(">> SpO2 historico...")
    await send("34fa", label="SpO2 historial")
    await asyncio.sleep(5)

    # ── Historial pasos ──────────────────────────────────────
    log(">> Historial pasos...")
    await send("b2fa", label="Pasos historico")
    await asyncio.sleep(5)

    # ── FC en vivo (10 segundos) ─────────────────────────────
    log(">> FC en vivo (10s)...")
    await asyncio.sleep(10)

    # ── Cerrar ───────────────────────────────────────────────
    if tok1: notify1.remove_value_changed(tok1)
    if tok2: notify2.remove_value_changed(tok2)
    session.close(); device.close()
    log("Conexion cerrada.")
    return True

# ── Guardar resultados ─────────────────────────────────────────
def save_results():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON completo
    json_path = os.path.join(OUTPUT_DIR, f"sync_{stamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(collected, f, indent=2, ensure_ascii=False)
    print(f"\nJSON guardado: {json_path}")

    # CSV de FC
    if collected["hr_history"]:
        hr_path = os.path.join(OUTPUT_DIR, f"hr_{stamp}.csv")
        with open(hr_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["datetime", "bpm"])
            w.writeheader(); w.writerows(collected["hr_history"])
        print(f"FC CSV: {hr_path} ({len(collected['hr_history'])} registros)")

    # CSV de SpO2
    if collected["spo2_history"]:
        sp_path = os.path.join(OUTPUT_DIR, f"spo2_{stamp}.csv")
        with open(sp_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["datetime", "spo2_pct"])
            w.writeheader(); w.writerows(collected["spo2_history"])
        print(f"SpO2 CSV: {sp_path} ({len(collected['spo2_history'])} registros)")

    # Resumen en pantalla
    print("\n" + "="*60)
    print("RESUMEN DE SINCRONIZACION")
    print("="*60)
    if collected["device"]:
        print(f"Dispositivo: {collected['device']}")
    if collected["battery_pct"] is not None:
        print(f"Bateria:     {collected['battery_pct']}%")
    if collected["live_hr_bpm"]:
        print(f"FC ahora:    {collected['live_hr_bpm']} bpm")
    if collected["steps_today"] is not None:
        print(f"Pasos hoy:   {collected['steps_today']}")
        print(f"Calorias:    {collected['calories_today']} kcal")
        print(f"Distancia:   {collected['distance_today_m']}m")
        print(f"Activo:      {collected['active_min_today']} min")
    print(f"FC hist:     {len(collected['hr_history'])} lecturas")
    print(f"SpO2 hist:   {len(collected['spo2_history'])} lecturas")

    # FC stats si hay datos
    if collected["hr_history"]:
        bpms = [r["bpm"] for r in collected["hr_history"]]
        print(f"\nFC estadisticas:")
        print(f"  Promedio: {sum(bpms)/len(bpms):.0f} bpm")
        print(f"  Maximo:   {max(bpms)} bpm")
        print(f"  Minimo:   {min(bpms)} bpm")
        zona_alta = [b for b in bpms if b > 110]
        print(f"  Tiempo en zona alta (>110): {len(zona_alta)*5} min")

    if collected["spo2_history"]:
        vals = [r["spo2_pct"] for r in collected["spo2_history"]]
        print(f"\nSpO2 estadisticas:")
        print(f"  Promedio: {sum(vals)/len(vals):.1f}%")
        print(f"  Minimo:   {min(vals)}%")
        print(f"  Normal (>=95%): {sum(1 for v in vals if v>=95)}/{len(vals)} lecturas")

# ── Main ───────────────────────────────────────────────────────
async def main():
    print("="*60)
    print("Air5 Smartwatch — Sincronizador")
    print(f"Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    print()
    print("IMPORTANTE: El reloj debe estar conectado en")
    print("Windows Settings > Bluetooth primero.")
    print()

    ok = await sync()
    if ok:
        save_results()
    else:
        print("\nSync fallido. Verifica que el reloj este conectado en Windows.")

asyncio.run(main())
