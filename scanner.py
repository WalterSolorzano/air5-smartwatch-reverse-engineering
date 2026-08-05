import asyncio
from bleak import BleakScanner

async def main():
    print("Escaneando dispositivos BLE por 10 segundos...")
    devices = await BleakScanner.discover(timeout=10.0)
    
    if not devices:
        print("No se encontraron dispositivos.")
    else:
        print(f"\nEncontrados {len(devices)} dispositivos:")
        for d in sorted(devices, key=lambda x: x.rssi if x.rssi else -999, reverse=True):
            print(f"  [{d.rssi:4d} dBm] {d.address}  -  {d.name or '(sin nombre)'}")

asyncio.run(main())
