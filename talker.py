import asyncio
from bleak import BleakClient

# Windows a veces formatea la MAC diferente, probamos ambas
MAC_ADDR = "81:0A:B7:00:1D:BC"

async def main():
    print(f"Intentando conectar directamente a {MAC_ADDR}...")
    print("(El dispositivo ya está en la lista de Windows como BTLE conocido)\n")
    
    try:
        async with BleakClient(MAC_ADDR, timeout=20.0) as client:
            if not client.is_connected:
                print("No se pudo conectar.")
                return
            
            print(f"✅ Conectado!")
            
            write_uuid = None
            notify_uuid = None
            
            print("\n[Servicios y características encontrados:]")
            for service in client.services:
                print(f"\n  Service: {service.uuid}")
                for char in service.characteristics:
                    print(f"    Char: {char.uuid} | Handle: 0x{char.handle:04x} | Props: {','.join(char.properties)}")
                    if char.handle == 0x0012:
                        write_uuid = char.uuid
                        print(f"      ^^^ WRITE CHAR (0x0012) ^^^")
                    if char.handle == 0x0014:
                        notify_uuid = char.uuid
                        print(f"      ^^^ NOTIFY CHAR (0x0014) ^^^")

            if not write_uuid or not notify_uuid:
                print("\n⚠️ No se encontraron los handles 0x0012/0x0014 por número.")
                print("Buscando por nombre de propiedad (write/notify)...")
                for service in client.services:
                    for char in service.characteristics:
                        if "write" in char.properties or "write-without-response" in char.properties:
                            write_uuid = write_uuid or char.uuid
                            print(f"  Posible Write: {char.uuid} (handle 0x{char.handle:04x})")
                        if "notify" in char.properties:
                            notify_uuid = notify_uuid or char.uuid
                            print(f"  Posible Notify: {char.uuid} (handle 0x{char.handle:04x})")

            print(f"\nUsando Write UUID: {write_uuid}")
            print(f"Usando Notify UUID: {notify_uuid}")

            if notify_uuid and write_uuid:
                responses = []
                
                def notification_handler(sender, data):
                    hex_val = data.hex()
                    responses.append(hex_val)
                    cmd = hex_val[:2]
                    payload = hex_val[2:]
                    print(f"⌚ Reloj responde [{cmd}]: {payload}")

                print("\n▶ Suscribiendo a notificaciones...")
                await client.start_notify(notify_uuid, notification_handler)
                await asyncio.sleep(1)
                
                print("\n📤 Enviando A1 (info del dispositivo)...")
                await client.write_gatt_char(write_uuid, bytearray.fromhex("a1"))
                await asyncio.sleep(2)
                
                print("\n📤 Enviando A2 (batería/estado)...")
                await client.write_gatt_char(write_uuid, bytearray.fromhex("a2"))
                await asyncio.sleep(2)

                print("\n📤 Enviando BB (estado general)...")
                await client.write_gatt_char(write_uuid, bytearray.fromhex("bb"))
                await asyncio.sleep(2)

                print("\n📤 Enviando 2601 (pasos hoy)...")
                await client.write_gatt_char(write_uuid, bytearray.fromhex("2601"))
                await asyncio.sleep(2)

                await client.stop_notify(notify_uuid)
                
                print(f"\n{'='*50}")
                print(f"Total respuestas recibidas: {len(responses)}")
                for i, r in enumerate(responses):
                    print(f"  {i+1}. {r}")
            else:
                print("\n❌ No se encontraron las características necesarias.")

    except Exception as e:
        print(f"\n❌ Error: {type(e).__name__}: {e}")

asyncio.run(main())
