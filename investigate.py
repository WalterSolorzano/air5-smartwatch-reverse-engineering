"""
Script de investigacion: 
- Desactiva recordatorio sedentario (D1 00 00)
- Prueba comandos A7 para SpO2
- Prueba 2600 (hoy, pagina 0) vs 2601
- Suscribe a AMBOS canales de notificacion
"""
import asyncio
import struct
from datetime import datetime

from winrt.windows.devices.bluetooth import BluetoothLEDevice, BluetoothConnectionStatus
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattCommunicationStatus, GattWriteOption,
    GattClientCharacteristicConfigurationDescriptorValue, GattSession,
)
from winrt.windows.storage.streams import DataWriter, DataReader

MAC_ADDR = "81:0A:B7:00:1D:BC"

def mac_to_int(mac): return int(mac.replace(":", ""), 16)

async def main():
    mac_int = mac_to_int(MAC_ADDR)
    print(f"Conectando...")
    device = await BluetoothLEDevice.from_bluetooth_address_async(mac_int)
    if not device:
        print("Dispositivo no encontrado"); return

    session = await GattSession.from_device_id_async(device.bluetooth_device_id)
    session.maintain_connection = True

    for i in range(60):
        if device.connection_status == BluetoothConnectionStatus.CONNECTED:
            print(f"Conectado en {i+1}s!"); break
        await asyncio.sleep(1)
    else:
        print("No conecta en 60s"); session.close(); device.close(); return

    services_result = await device.get_gatt_services_async()
    write1 = notify1 = write2 = notify2 = None
    for svc in services_result.services:
        cr = await svc.get_characteristics_async()
        if cr.status != GattCommunicationStatus.SUCCESS: continue
        for ch in cr.characteristics:
            h = ch.attribute_handle
            if h == 0x0011: write1 = ch
            if h == 0x0013: notify1 = ch
            if h == 0x0017: write2 = ch
            if h == 0x0019: notify2 = ch

    print(f"Ch1 write={write1 is not None} notify={notify1 is not None}")
    print(f"Ch2 write={write2 is not None} notify={notify2 is not None}")

    def decode_notification(data, channel):
        cmd = data[0:1].hex().upper()
        payload = data[1:]
        if cmd == "E5" and len(payload) >= 3:
            sub = payload[0]
            if sub == 0x11:
                bpm = payload[2] if len(payload) > 2 else 0
                print(f"  [Ch{channel}->E5] FC en vivo: {bpm} bpm")
                return
            elif sub == 0x12:
                spo2 = payload[2] if len(payload) > 2 else 0
                print(f"  [Ch{channel}->E5] SpO2 en vivo: {spo2}% !")
                return
        elif cmd == "26":
            if len(payload) >= 8:
                # Intentar diferentes offsets para pasos
                for off in range(0, min(6, len(payload)-1)):
                    steps_le = struct.unpack_from('<H', payload, off)[0]
                    steps_be = struct.unpack_from('>H', payload, off)[0]
                    if 100 < steps_le < 50000:
                        print(f"  [Ch{channel}->26] offset {off} LE: {steps_le} pasos?")
                    if 100 < steps_be < 50000 and steps_be != steps_le:
                        print(f"  [Ch{channel}->26] offset {off} BE: {steps_be} pasos?")
        elif cmd == "A7":
            spo2 = payload[0] if payload else 0
            print(f"  [Ch{channel}->A7] SpO2: {spo2}%")
        else:
            print(f"  [Ch{channel}->{cmd}]: {payload.hex()}")

    all_responses = []

    def make_handler(channel):
        def handler(sender, args):
            reader = DataReader.from_buffer(args.characteristic_value)
            data = bytes([reader.read_byte() for _ in range(reader.unconsumed_buffer_length)])
            all_responses.append((channel, data))
            decode_notification(data, channel)
        return handler

    # Suscribir ambos canales
    tok1 = tok2 = None
    if notify1:
        r = await notify1.write_client_characteristic_configuration_descriptor_async(
            GattClientCharacteristicConfigurationDescriptorValue.NOTIFY)
        if r == GattCommunicationStatus.SUCCESS:
            tok1 = notify1.add_value_changed(make_handler(1))
            print("Suscrito a notificaciones Ch1 OK")

    if notify2:
        r = await notify2.write_client_characteristic_configuration_descriptor_async(
            GattClientCharacteristicConfigurationDescriptorValue.NOTIFY)
        if r == GattCommunicationStatus.SUCCESS:
            tok2 = notify2.add_value_changed(make_handler(2))
            print("Suscrito a notificaciones Ch2 OK")

    async def send(label, hex_data, channel=1):
        w = write1 if channel == 1 else write2
        if not w:
            print(f"  No hay write para ch{channel}"); return
        print(f"\n>> {label} (ch{channel}): {hex_data}")
        writer = DataWriter()
        writer.write_bytes(bytearray.fromhex(hex_data))
        buf = writer.detach_buffer()
        try: await w.write_value_with_result_async(buf)
        except TypeError: await w.write_value_async(buf, GattWriteOption.WRITE_WITHOUT_RESPONSE)
        await asyncio.sleep(2)

    # === DESACTIVAR RECORDATORIO SEDENTARIO ===
    await send("D1 (desactivar sedentario)", "d10000")
    await send("D7 (ventana sedentario OFF)", "d7000000000000")

    # === SYNC HORA ===
    now = datetime.now()
    time_hex = f"a3{now.year:04x}{now.month:02x}{now.day:02x}{now.hour:02x}{now.minute:02x}{now.second:02x}"
    await send("A3 (hora actual)", time_hex)

    # === DATOS BASICOS ===
    await send("A2 (bateria)", "a2")
    await send("BB (estado)", "bb")

    # === PROBAR DIFERENTES PAGINAS DE PASOS ===
    print("\n--- Investigando formato de pasos ---")
    await send("2600 (pasos pag 0?)", "2600")
    await send("2601 (pasos pag 1)", "2601")
    await send("2602 (pasos pag 2?)", "2602")

    # === PROBAR SPO2 ===
    print("\n--- Investigando SpO2 ---")
    await send("A7 (SpO2?)", "a7")
    await send("A7 01 (SpO2 start?)", "a701")
    await asyncio.sleep(5)  # Esperar respuesta SpO2

    # === FC EN VIVO ===
    print("\n--- Esperando FC en vivo (E5) 10s ---")
    await asyncio.sleep(10)

    # Cleanup
    if tok1: notify1.remove_value_changed(tok1)
    if tok2: notify2.remove_value_changed(tok2)
    session.close(); device.close()

    print(f"\n{'='*60}")
    print(f"TOTAL RESPUESTAS: {len(all_responses)}")
    seen = set()
    for ch, d in all_responses:
        key = d[:2].hex()
        if key not in seen:
            seen.add(key)
            print(f"  [Ch{ch}|{d[0:1].hex().upper()}]: {d.hex()}")

asyncio.run(main())
