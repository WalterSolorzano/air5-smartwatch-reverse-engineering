import struct
import sys

def convert(in_path, out_path):
    with open(in_path, 'rb') as f_in, open(out_path, 'wb') as f_out:
        header = f_in.read(16)
        if not header.startswith(b'btsnoop'):
            print("Invalid header")
            return
        
        # Escribir el header estándar (btsnoop\0, version 1, datalink 1002)
        f_out.write(b'btsnoop\x00\x00\x00\x00\x01\x00\x00\x03\xea')
        
        packets_converted = 0
        while True:
            # Leer el header del paquete custom de Android (little-endian)
            # 2 bytes orig_len, 2 bytes inc_len, 4 bytes flags, 4 bytes drops, 8 bytes time
            buf = f_in.read(20)
            if len(buf) < 20:
                break
                
            orig_len, inc_len, flags, drops, ts = struct.unpack('<HHIIQ', buf)
            
            data = f_in.read(inc_len)
            if len(data) < inc_len:
                break
                
            # Convertir a estándar btsnoop (big-endian)
            # flags en estándar btsnoop: 
            # 0x01 = sent/received, 0x02 = data/command
            # Vamos a pasar los flags originales tal cual pero en big-endian (Wireshark suele tragárselo si el datalink es HCI)
            std_header = struct.pack('>IIIIQ', orig_len, inc_len, flags, drops, ts)
            
            f_out.write(std_header)
            f_out.write(data)
            packets_converted += 1
            
        print(f"¡Convertidos {packets_converted} paquetes exitosamente!")

if __name__ == '__main__':
    convert('btsnoop_hci.log', 'btsnoop_fixed.pcap')
