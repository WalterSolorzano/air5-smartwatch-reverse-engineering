import asyncio
import struct
from datetime import datetime

from winrt.windows.devices.bluetooth import BluetoothLEDevice, BluetoothConnectionStatus
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattCommunicationStatus,
    GattWriteOption,
    GattClientCharacteristicConfigurationDescriptorValue,
    GattSession,
)
from winrt.windows.storage.streams import DataWriter, DataReader

MAC_ADDR = "81:0A:B7:00:1D:BC"

def mac_to_int(mac: str) -> int:
    return int(mac.replace(":", ""), 16)

def decode_response(cmd: str, data: bytes):
    """Decodifica la respuesta del reloj segun el comando."""
    if cmd == "A1":
        try:
            serial = data.decode("ascii")
            return f"Numero de serie: {serial}"
        except:
            return f"Raw: {data.hex()}"
    elif cmd == "A2":
        pct = data[0] if data else 0
        return f"Bateria: {pct}%"
    elif cmd == "BB":
        return f"Estado: {'OK' if data[0] == 1 else 'Error'}"
    elif cmd == "26":
        if len(data) >= 19:
            page = data[0]
            # Pasos del dia: bytes 2-3 little-endian
            steps = struct.unpack_from("<H", data, 2)[0]
            # Calorias: bytes 4-5
            cal   = struct.unpack_from("<H", data, 4)[0]
            # Distancia metros: bytes 6-7
            dist  = struct.unpack_from("<H", data, 6)[0]
            # Tiempo activo: byte 8
            active_min = data[8]
            return (f"Pasos hoy: {steps} | Calorias: {cal} kcal | "
                    f"Distancia: {dist}m | Tiempo activo: {active_min}min")
        return f"Raw: {data.hex()}"
    elif cmd == "BE":
        page = data[0] if data else 0
        # Cada pagina tiene hasta 9 registros de 2 bytes (pasos por bloque horario)
        entries = []
        for i in range(1, len(data)-1, 2):
            val = struct.unpack_from("<H", data, i)[0]
            if val > 0:
                entries.append(f"bloque{(i-1)//2+1}:{val}")
        return f"Historico pag {page}: {', '.join(entries) if entries else '(sin datos)'}"
    return f"Raw: {data.hex()}"

async def main():
    mac_int = mac_to_int(MAC_ADDR)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Conectando a Air5(ID-1DBC)...")

    device = await BluetoothLEDevice.from_bluetooth_address_async(mac_int)
    if device is None:
        print("ERROR: Windows no conoce este dispositivo.")
        return

    session = await GattSession.from_device_id_async(device.bluetooth_device_id)
    session.maintain_connection = True
    print("GattSession abierta. Esperando conexion...")

    for i in range(60):
        if device.connection_status == BluetoothConnectionStatus.CONNECTED:
            print(f"Conectado en {i+1}s!")
            break
        await asyncio.sleep(1)
    else:
        print("No se pudo conectar en 60 segundos.")
        session.close(); device.close(); return

    services_result = await device.get_gatt_services_async()
    write_char = None
    notify_char = None
    for service in services_result.services:
        chars_result = await service.get_characteristics_async()
        if chars_result.status != GattCommunicationStatus.SUCCESS:
            continue
        for char in chars_result.characteristics:
            h = char.attribute_handle
            if h == 0x0011: write_char = char
            if h == 0x0013: notify_char = char

    if not write_char or not notify_char:
        print("No se encontraron las caracteristicas.")
        session.close(); device.close(); return

    result = await notify_char.write_client_characteristic_configuration_descriptor_async(
        GattClientCharacteristicConfigurationDescriptorValue.NOTIFY
    )
    if result != GattCommunicationStatus.SUCCESS:
        print(f"Error suscribiendo: {result}")
        session.close(); device.close(); return

    responses = {}
    def on_notification(sender, args):
        reader = DataReader.from_buffer(args.characteristic_value)
        data = bytes([reader.read_byte() for _ in range(reader.unconsumed_buffer_length)])
        cmd  = data[0:1].hex().upper()
        payload = data[1:]
        decoded = decode_response(cmd, payload)
        responses[cmd] = payload
        print(f"  [Watch->{cmd}] {decoded}")

    token = notify_char.add_value_changed(on_notification)

    async def send(label, hex_data):
        writer = DataWriter()
        writer.write_bytes(bytearray.fromhex(hex_data))
        buf = writer.detach_buffer()
        try:
            res = await write_char.write_value_with_result_async(buf)
        except TypeError:
            res = await write_char.write_value_async(buf, GattWriteOption.WRITE_WITHOUT_RESPONSE)
        print(f"\n>> {label}")
        await asyncio.sleep(2)

    await send("A1 - Info dispositivo", "a1")
    await send("A2 - Bateria", "a2")
    await send("BB - Estado", "bb")
    await send("2601 - Pasos del dia", "2601")
    await send("BE01 - Historial pag 1", "be01")
    await send("BE02 - Historial pag 2", "be02")
    await send("BE03 - Historial pag 3", "be03")
    await send("BE04 - Historial pag 4", "be04")

    print(f"\n{'='*60}")
    print(f"RESUMEN - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    for cmd, data in responses.items():
        print(f"  [{cmd}] {decode_response(cmd, data)}")

    notify_char.remove_value_changed(token)  # usar token, no funcion
    session.close()
    device.close()
    print("\nConexion cerrada correctamente.")

asyncio.run(main())
