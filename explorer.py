import asyncio
from bleak import BleakClient

MAC_ADDRESS = "81:0A:B7:00:1D:BC"

async def explore():
    print(f"Intentando conectar al reloj Air5 (MAC: {MAC_ADDRESS})...")
    print("Esto puede tardar unos segundos...")
    
    try:
        async with BleakClient(MAC_ADDRESS, timeout=20.0) as client:
            print(f"¡Conectado exitosamente: {client.is_connected}!")
            
            print("\nExplorando los puertos de comunicación (Servicios y Características)...")
            print("=" * 60)
            
            for service in client.services:
                print(f"\n[Servicio] {service.uuid} - {service.description}")
                for char in service.characteristics:
                    props = ",".join(char.properties)
                    print(f"  └─ [Característica] {char.uuid} ({props})")
                    print(f"       Descripción: {char.description}")
                    
            print("\n" + "=" * 60)
            print("Exploración finalizada. Guarda estos UUIDs, los necesitaremos para escuchar los datos.")
            
    except Exception as e:
        print(f"\nError al conectar o leer: {e}")
        print("Si da error, a veces ayuda eliminar el emparejamiento en Windows y volver a emparejar, o simplemente ejecutar el script otra vez.")

if __name__ == "__main__":
    asyncio.run(explore())
