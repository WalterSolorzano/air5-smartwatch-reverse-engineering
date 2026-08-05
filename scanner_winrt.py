"""
Scan usando WinRT directamente con radio de Barrot, escuchando publicidades BLE.
"""
import asyncio
from winrt.windows.devices.bluetooth.advertisement import (
    BluetoothLEAdvertisementWatcher,
    BluetoothLEScanningMode
)
from winrt.windows.devices.bluetooth import BluetoothLEDevice

TARGET_MAC_INT = 0x810AB7001DBC
TARGET_MAC_STR = "81:0A:B7:00:1D:BC"

found = False

def mac_int_to_str(mac_int):
    hex_str = f"{mac_int:012X}"
    return ":".join(hex_str[i:i+2] for i in range(0, 12, 2))

seen = set()
def on_received(watcher, args):
    global found
    addr = args.bluetooth_address
    mac_str = mac_int_to_str(addr)
    
    try:
        rssi = args.raw_signal_strength_in_d_bm
    except AttributeError:
        try:
            rssi = args.rssi
        except AttributeError:
            rssi = 0
    
    name = ""
    try:
        local_name = args.advertisement.local_name
        if local_name:
            name = local_name
    except:
        pass
    
    # Mostrar cada dispositivo solo una vez
    if mac_str not in seen:
        seen.add(mac_str)
        print(f"  [{rssi:4d} dBm] {mac_str}  {name}")
    
    if addr == TARGET_MAC_INT:
        found = True
        print(f"\n!!! ENCONTRADO EL RELOJ: {mac_str} !!!")

async def main():
    print(f"Escaneando BLE durante 20 segundos...")
    print(f"Buscando: {TARGET_MAC_STR}\n")
    
    watcher = BluetoothLEAdvertisementWatcher()
    watcher.scanning_mode = BluetoothLEScanningMode.ACTIVE
    watcher.add_received(on_received)
    watcher.start()
    
    await asyncio.sleep(20)
    
    watcher.stop()
    
    if not found:
        print(f"\nEl reloj NO se encontro en 20 segundos.")
        print("Posibles causas:")
        print("  1. El reloj esta en directed advertising (solo acepta reconexion del celular emparejado)")
        print("  2. El Barrot no esta detectando BLE correctamente")
        print("  3. El reloj esta fuera de rango o apagado")
    else:
        print(f"\nEl reloj esta anunciandose. Podemos conectar!")

asyncio.run(main())
