import asyncio
import logging
from bleak import BleakScanner

# Habilitar logs de depuración para ver qué hace Bleak por debajo
logging.basicConfig(level=logging.DEBUG)

async def run():
    print("Iniciando escaneo con modo de depuración activado...")
    try:
        devices = await BleakScanner.discover(timeout=5.0)
        print(f"\nCantidad de dispositivos crudos encontrados: {len(devices)}")
        for d in devices:
            print(f"[{d.address}] - {d.name}")
    except Exception as e:
        print(f"Error durante el escaneo: {e}")

if __name__ == "__main__":
    asyncio.run(run())
