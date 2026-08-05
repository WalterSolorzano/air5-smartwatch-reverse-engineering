import asyncio
from bleak import BleakClient

MAC_ADDRESS = "81:0A:B7:00:1D:BC"

# UUIDs exactos sacados de tus capturas de pantalla del Air5
UART_SERVICE_UUID = "0000d0ff-3c17-d293-8e48-14fe2e4da212"
UART_TX_CHAR_UUID = "0000ffe1-3c17-d293-8e48-14fe2e4da212" # SimpleKeyState (Notify)
UART_RX_CHAR_UUID = "0000ffe0-3c17-d293-8e48-14fe2e4da212" # Write

def notification_handler(sender, data):
    """Esta función se ejecuta cada vez que el reloj envía datos por FFE1"""
    hex_data = data.hex(':')
    print(f"\n[RELOJ FFE1] Datos recibidos: {hex_data}", flush=True)

async def listen_and_talk():
    print(f"Intentando conectar al reloj Air5 (MAC: {MAC_ADDRESS})...", flush=True)
    # Nota: asegúrate de haber cerrado el Bluetooth LE Explorer para liberar la conexión
    
    try:
        async with BleakClient(MAC_ADDRESS, timeout=20.0) as client:
            print(f"¡Conectado! Estado: {client.is_connected}", flush=True)
            
            print(f"Suscribiéndonos a las notificaciones en {UART_TX_CHAR_UUID}...", flush=True)
            await client.start_notify(UART_TX_CHAR_UUID, notification_handler)
            print("Suscripción activa. Escuchando el tráfico del reloj... (Presiona Ctrl+C para salir)", flush=True)
            print(">> Prueba a tocar la pantalla del reloj o cambiar de menú para ver si emite datos <<\n", flush=True)
            
            while True:
                await asyncio.sleep(1)
                
    except Exception as e:
        print(f"Error al conectar: {e}")
        print("Si falla, puede ser porque Windows requiere que esté emparejado (o desemparejado) dependiendo de la caché de WinRT.")

if __name__ == "__main__":
    try:
        asyncio.run(listen_and_talk())
    except KeyboardInterrupt:
        print("\nDesconectando...")
