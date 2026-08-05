"""
Script rapido: conecta y envia D1 con el valor que usa GloryFit para
resetear el recordatorio sedentario a un intervalo normal (60 min).
"""
import asyncio
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
    device = await BluetoothLEDevice.from_bluetooth_address_async(mac_to_int(MAC_ADDR))
    if not device: print("No encontrado"); return

    session = await GattSession.from_device_id_async(device.bluetooth_device_id)
    session.maintain_connection = True

    for i in range(30):
        if device.connection_status == BluetoothConnectionStatus.CONNECTED:
            print(f"Conectado en {i+1}s!"); break
        await asyncio.sleep(1)
    else:
        print("No conecta"); session.close(); device.close(); return

    services_result = await device.get_gatt_services_async()
    write1 = notify1 = None
    for svc in services_result.services:
        cr = await svc.get_characteristics_async()
        if cr.status != GattCommunicationStatus.SUCCESS: continue
        for ch in cr.characteristics:
            if ch.attribute_handle == 0x0011: write1 = ch
            if ch.attribute_handle == 0x0013: notify1 = ch

    def on_notify(sender, args):
        reader = DataReader.from_buffer(args.characteristic_value)
        data = bytes([reader.read_byte() for _ in range(reader.unconsumed_buffer_length)])
        cmd = data[0:1].hex().upper()
        if cmd == "A2":
            pct = data[1] if len(data) > 1 else 0
            print(f"  Bateria: {pct}% (hex: {data[1:2].hex()})")
        elif cmd == "E5" and len(data) >= 4:
            bpm = data[3]
            print(f"  FC en vivo: {bpm} bpm")
        else:
            print(f"  [{cmd}]: {data[1:].hex()}")

    tok = None
    if notify1:
        r = await notify1.write_client_characteristic_configuration_descriptor_async(
            GattClientCharacteristicConfigurationDescriptorValue.NOTIFY)
        if r == GattCommunicationStatus.SUCCESS:
            tok = notify1.add_value_changed(on_notify)
            print("Notificaciones activas")

    async def send(label, hex_data):
        if not write1: return
        print(f"\n>> {label}: {hex_data}")
        writer = DataWriter()
        writer.write_bytes(bytearray.fromhex(hex_data))
        buf = writer.detach_buffer()
        try: await write1.write_value_with_result_async(buf)
        except TypeError: await write1.write_value_async(buf, GattWriteOption.WRITE_WITHOUT_RESPONSE)
        await asyncio.sleep(1.5)

    # Bateria actual
    await send("A2 (bateria)", "a2")

    # D1: recordatorio sedentario cada 60 min (0x3c = 60)
    # El "00" al final puede ser enable/disable: GloryFit usa "00" en d10a00
    await send("D1 3c 00 (sedentario cada 60min)", "d13c00")
    await asyncio.sleep(0.5)
    # Tambie probar con d1 00 (solo disable)
    await send("D1 00 (solo byte, intent disable)", "d100")

    # D7: ventana del sedentario completamente apagada (todos ceros = sin ventana)
    await send("D7 (sin ventana sedentario)", "d7000000000000")

    # Esperar por si manda algo
    print("\nEsperando notificaciones 5s...")
    await asyncio.sleep(5)

    if tok: notify1.remove_value_changed(tok)
    session.close(); device.close()
    print("\nFin.")

asyncio.run(main())
