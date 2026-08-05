import zipfile
import os
import shutil

def extract_btsnoop():
    zip_path = "bugreport.zip"
    target_filename = "btsnoop_hci.log"
    found = False
    
    if not os.path.exists(zip_path):
        print(f"Error: {zip_path} no encontrado.")
        return

    print("Analizando el archivo ZIP...")
    with zipfile.ZipFile(zip_path, 'r') as z:
        for info in z.infolist():
            lower_name = info.filename.lower()
            if 'snoop' in lower_name or 'bluetooth' in lower_name or '.cfa' in lower_name:
                print(f"Posible log encontrado: {info.filename}")
                if 'btsnoop' in lower_name or '.cfa' in lower_name or 'hci' in lower_name:
                    z.extract(info, "extracted_logs")
                    final_path = os.path.join(os.getcwd(), info.filename.split('/')[-1])
                    shutil.copy(os.path.join("extracted_logs", info.filename), final_path)
                    print(f"-> Guardado como: {final_path}")
                    found = True
                
                # Move to current directory
                extracted_path = os.path.join("extracted_logs", info.filename)
                final_path = os.path.join(os.getcwd(), os.path.basename(info.filename))
                
                # Si hay múltiples logs (ej. .log.old), los guardamos todos con su nombre
                final_path = os.path.join(os.getcwd(), info.filename.split('/')[-1])
                shutil.copy(extracted_path, final_path)
                print(f"Guardado como: {final_path}")
                found = True

    if not found:
        print("No se encontró ningún archivo btsnoop_hci.log en el reporte.")
    else:
        print("¡Extracción completada!")

if __name__ == "__main__":
    extract_btsnoop()
