"""
╔══════════════════════════════════════════════════════════════════════════╗
║                       VITAMON HEALTH COMPANION                           ║
║             Smartwatch Air5 (ID-1DBC) BLE Floating Widget & Tamagotchi   ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
import sys
import os
import time
import math
import random
import threading
import queue
import struct
import json
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk

# ── Configuración de Apariencia ──────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

MAC_ADDR = "81:0A:B7:00:1D:BC"
SYNC_DIR = r"C:\Users\--X\Music\bluetooth\sync_data"
os.makedirs(SYNC_DIR, exist_ok=True)

# Paleta de Colores Cyberpunk / Modern Glassmorphism
PALETTE = {
    "bg_dark": "#0B0F19",
    "card_bg": "#151C2C",
    "card_border": "#232F48",
    "card_sub": "#1A2338",
    "neon_teal": "#00F2FE",
    "neon_blue": "#4FACFE",
    "neon_rose": "#FF2A6D",
    "neon_green": "#05FFA1",
    "neon_amber": "#FFBE0B",
    "neon_purple": "#7B2CBF",
    "text_main": "#F8FAFC",
    "text_muted": "#94A3B8",
}

# ── Motor de Conexión BLE WinRT (Background Thread) ─────────────────────
class BLEBridgeThread(threading.Thread):
    def __init__(self, data_queue, cmd_queue, mac_addr=MAC_ADDR):
        super().__init__(daemon=True)
        self.data_queue = data_queue
        self.cmd_queue = cmd_queue
        self.mac_addr = mac_addr
        self.running = True
        self.connected = False
        self.loop = None

    def run(self):
        import asyncio
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.ble_worker())
        except Exception as e:
            self.data_queue.put({"type": "status", "status": "error", "msg": str(e)})

    async def ble_worker(self):
        try:
            from winrt.windows.devices.bluetooth import BluetoothLEDevice, BluetoothConnectionStatus
            from winrt.windows.devices.bluetooth.genericattributeprofile import (
                GattCommunicationStatus, GattWriteOption,
                GattClientCharacteristicConfigurationDescriptorValue
            )
            from winrt.windows.storage.streams import DataWriter, DataReader
        except ImportError:
            self.data_queue.put({"type": "status", "status": "error", "msg": "WinRT BLE no disponible"})
            return

        mac_int = int(self.mac_addr.replace(":", ""), 16)

        while self.running:
            self.data_queue.put({"type": "status", "status": "connecting", "msg": "Conectando al Air5..."})
            try:
                device = await BluetoothLEDevice.from_bluetooth_address_async(mac_int)
                if not device:
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "Dispositivo no encontrado"})
                    await asyncio.sleep(5)
                    continue

                services_res = await device.get_gatt_services_async()
                if services_res.status != GattCommunicationStatus.SUCCESS:
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "Fallo al solicitar servicios GATT"})
                    device.close()
                    await asyncio.sleep(4)
                    continue

                write1 = notify1 = write2 = notify2 = None
                for svc in services_res.services:
                    chars_res = await svc.get_characteristics_async()
                    if chars_res.status != GattCommunicationStatus.SUCCESS:
                        continue
                    for ch in chars_res.characteristics:
                        h = ch.attribute_handle
                        if h == 0x0011: write1 = ch
                        if h == 0x0013: notify1 = ch
                        if h == 0x0017: write2 = ch
                        if h == 0x0019: notify2 = ch

                if not write1 or not notify1:
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "Canales GATT no disponibles"})
                    device.close()
                    await asyncio.sleep(4)
                    continue

                # Handlers de notificación
                def on_ch1_notify(sender, args):
                    reader = DataReader.from_buffer(args.characteristic_value)
                    data = bytes([reader.read_byte() for _ in range(reader.unconsumed_buffer_length)])
                    if not data: return
                    cmd = data[0]

                    # Bateria (A2)
                    if cmd == 0xA2 and len(data) >= 2:
                        self.data_queue.put({"type": "battery", "value": data[1]})

                    # FC en vivo (E5)
                    elif cmd == 0xE5 and len(data) >= 4 and data[1] == 0x11:
                        bpm = data[3]
                        if 35 <= bpm <= 220:
                            self.data_queue.put({"type": "live_hr", "value": bpm})

                    # Pasos acumulados hoy (26)
                    elif cmd == 0x26 and len(data) >= 9:
                        steps = struct.unpack_from("<H", data, 3)[0]
                        calories = struct.unpack_from("<H", data, 5)[0]
                        distance = struct.unpack_from("<H", data, 7)[0]
                        active_min = data[9] if len(data) > 9 else 0
                        if steps != 65535:  # Validar no sea máscara
                            self.data_queue.put({
                                "type": "daily_activity",
                                "steps": steps,
                                "calories": calories,
                                "distance": distance,
                                "active_min": active_min
                            })

                    # Incremento de pasos en vivo por hora (B1)
                    elif cmd == 0xB1 and len(data) >= 18:
                        step_inc = struct.unpack_from("<H", data, 7)[0]
                        self.data_queue.put({"type": "step_inc", "value": step_inc})

                    # Historial FC (F7)
                    elif cmd == 0xF7 and len(data) >= 7:
                        year = struct.unpack_from(">H", data, 1)[0]
                        month, day, page = data[3], data[4], data[5]
                        bpms = [b for b in list(data[6:]) if 35 < b < 220]
                        if bpms:
                            self.data_queue.put({"type": "hr_hist_chunk", "year": year, "month": month, "day": day, "page": page, "bpms": bpms})

                def on_ch2_notify(sender, args):
                    reader = DataReader.from_buffer(args.characteristic_value)
                    data = bytes([reader.read_byte() for _ in range(reader.unconsumed_buffer_length)])
                    if not data: return
                    cmd = data[0]
                    # SpO2 (34)
                    if cmd == 0x34 and len(data) >= 20 and data[1] == 0xFA:
                        spo2 = data[-1]
                        if 70 <= spo2 <= 100 and spo2 != 0xFF:
                            self.data_queue.put({"type": "live_spo2", "value": spo2})
                    # Device Info (38)
                    elif cmd == 0x38:
                        try:
                            name = data[2:16].decode("ascii").rstrip("\x00")
                            mac = ":".join(f"{b:02X}" for b in data[22:28])
                            fw = f"{data[28]}.{data[29]}.{data[30]}" if len(data) > 30 else "1.0"
                            self.data_queue.put({"type": "device_info", "name": name, "mac": mac, "firmware": fw})
                        except: pass

                # Suscribirse
                await notify1.write_client_characteristic_configuration_descriptor_async(
                    GattClientCharacteristicConfigurationDescriptorValue.NOTIFY
                )
                tok1 = notify1.add_value_changed(on_ch1_notify)

                if notify2:
                    await notify2.write_client_characteristic_configuration_descriptor_async(
                        GattClientCharacteristicConfigurationDescriptorValue.NOTIFY
                    )
                    tok2 = notify2.add_value_changed(on_ch2_notify)

                async def send_cmd(w, hex_str):
                    if not w: return
                    wr = DataWriter()
                    wr.write_bytes(bytearray.fromhex(hex_str))
                    buf = wr.detach_buffer()
                    try: await w.write_value_with_result_async(buf)
                    except: await w.write_value_async(buf, GattWriteOption.WRITE_WITHOUT_RESPONSE)
                    await asyncio.sleep(0.1)

                # 1. Handshake inicial
                await send_cmd(write1, "0808442a01243943756ffffed921005f784be1dc")
                if write2:
                    await send_cmd(write2, "00f4000000000000000000000000000000000402")

                # 2. SILENCIAR SPAM DE SEDENTARISMO INMEDIATAMENTE
                await send_cmd(write1, "d1ff64")  # Intervalo 255 min, 100 pasos
                await send_cmd(write1, "d7160017000000")  # Ventana 22-23h

                # 3. Sincronizar hora actual
                now = datetime.now()
                time_hex = f"a3{now.year:04x}{now.month:02x}{now.day:02x}{now.hour:02x}{now.minute:02x}{now.second:02x}"
                await send_cmd(write1, time_hex)

                # 4. Solicitar datos iniciales
                await send_cmd(write1, "a2")    # Bateria
                await send_cmd(write1, "2601")  # Pasos de hoy
                if write2:
                    await send_cmd(write2, "34fa")  # SpO2

                self.connected = True
                self.data_queue.put({"type": "status", "status": "connected", "msg": "Air5 Conectado en Tiempo Real"})

                # Loop de sondeo periódico y comandos salientes
                last_poll = time.time()
                while self.running:
                    # Revisar si la UI envió comandos para el reloj
                    try:
                        while not self.cmd_queue.empty():
                            cmd_req = self.cmd_queue.get_nowait()
                            action = cmd_req.get("action")
                            if action == "silence_sedentary":
                                await send_cmd(write1, "d1ff64")
                                await send_cmd(write1, "d7160017000000")
                            elif action == "sync_time":
                                n = datetime.now()
                                th = f"a3{n.year:04x}{n.month:02x}{n.day:02x}{n.hour:02x}{n.minute:02x}{n.second:02x}"
                                await send_cmd(write1, th)
                            elif action == "vibrate":
                                await send_cmd(write1, "d201")
                            elif action == "send_hex":
                                await send_cmd(write1, cmd_req.get("hex", ""))
                    except queue.Empty:
                        pass

                    # Sondeo suave cada 15s para actualizar pasos y batería
                    if time.time() - last_poll > 15:
                        last_poll = time.time()
                        await send_cmd(write1, "a2")
                        await send_cmd(write1, "2601")

                    await asyncio.sleep(0.5)

            except Exception as e:
                self.connected = False
                self.data_queue.put({"type": "status", "status": "disconnected", "msg": f"Desconectado: {str(e)}"})
                await asyncio.sleep(4)


# ── Motor del Tamagotchi (Canvas Creature) ──────────────────────────────
class VitamonCreature:
    """Criatura virtual animada con estados emocionales, animaciones procedurales y evolución RPG."""
    def __init__(self, canvas):
        self.canvas = canvas
        self.state = "happy"  # happy, hyper, sleepy, meditating, cheering
        self.frame = 0
        self.level = 1
        self.xp = 0
        self.xp_to_next = 500
        self.mood_pct = 100
        self.body_battery = 85
        self.evolution_names = [
            "Huevito Cyber",      # Lvl 1
            "Aero Sprite",        # Lvl 2-4
            "Mecha Zorro",        # Lvl 5-9
            "Cyber Dragón Cósmico" # Lvl 10+
        ]

    def update_stats(self, steps, hr, active_min):
        """Calcula nivel, evolución y estado emocional a partir de datos fisiológicos."""
        # XP y Nivel
        self.xp = steps + (active_min * 20)
        self.level = max(1, 1 + self.xp // 500)
        self.xp_to_next = self.level * 500

        # Determinación de estado del Tamagotchi
        if hr > 105:
            self.state = "hyper"
        elif hr < 60 and steps == 0:
            self.state = "sleepy"
        elif steps > 3000 and hr > 85:
            self.state = "cheering"
        elif 60 <= hr <= 90:
            self.state = "happy"
        else:
            self.state = "meditating"

    def get_stage_name(self):
        if self.level == 1: return self.evolution_names[0]
        elif self.level < 5: return self.evolution_names[1]
        elif self.level < 10: return self.evolution_names[2]
        else: return self.evolution_names[3]

    def render(self, cx=110, cy=85):
        """Dibuja la criatura en el canvas según su estado y nivel con micro-animaciones."""
        self.canvas.delete("all")
        self.frame += 1
        f = self.frame

        # Respiración / rebote vertical
        bounce = int(math.sin(f * 0.25) * 4)
        aura_r = 50 + int(math.sin(f * 0.2) * 5)

        # 1. Aura de energía de fondo
        if self.state == "hyper":
            aura_color = "#FF2A6D"
            self.canvas.create_oval(cx - aura_r - 8, cy - aura_r - 8 + bounce,
                                    cx + aura_r + 8, cy + aura_r + 8 + bounce,
                                    fill="", outline=aura_color, width=2)
        elif self.state == "cheering":
            aura_color = "#05FFA1"
            self.canvas.create_oval(cx - aura_r - 5, cy - aura_r - 5 + bounce,
                                    cx + aura_r + 5, cy + aura_r + 5 + bounce,
                                    fill="", outline=aura_color, width=1.5)
        else:
            aura_color = "#1E293B"

        # 2. Sombra en el suelo
        self.canvas.create_oval(cx - 35, cy + 45, cx + 35, cy + 55, fill="#0D111A", outline="")

        # 3. Dibujo de la Criatura según su Etapa de Evolución
        if self.level == 1:
            # ── ETAPA 1: HUEVITO CYBER ──
            # Cuerpo de huevo
            self.canvas.create_oval(cx - 28, cy - 35 + bounce, cx + 28, cy + 35 + bounce,
                                    fill="#4FACFE", outline="#00F2FE", width=3)
            # Grietas de luz cibernética
            self.canvas.create_line(cx - 15, cy - 5 + bounce, cx, cy + 10 + bounce, fill="#00F2FE", width=2)
            self.canvas.create_line(cx, cy + 10 + bounce, cx + 18, cy + bounce, fill="#00F2FE", width=2)
            # Ojos / Visor
            self.canvas.create_oval(cx - 14, cy - 12 + bounce, cx - 4, cy - 4 + bounce, fill="#0B0F19", outline="#00F2FE")
            self.canvas.create_oval(cx + 4, cy - 12 + bounce, cx + 14, cy - 4 + bounce, fill="#0B0F19", outline="#00F2FE")

        elif self.level < 5:
            # ── ETAPA 2: AERO SPRITE (Gatito / Fantasmita Flotante) ──
            body_color = "#00F2FE" if self.state != "hyper" else "#FF2A6D"
            # Orejitas puntiagudas
            self.canvas.create_polygon([cx - 28, cy - 20 + bounce, cx - 36, cy - 48 + bounce, cx - 12, cy - 32 + bounce],
                                       fill=body_color, outline="#F8FAFC", width=1.5)
            self.canvas.create_polygon([cx + 28, cy - 20 + bounce, cx + 36, cy - 48 + bounce, cx + 12, cy - 32 + bounce],
                                       fill=body_color, outline="#F8FAFC", width=1.5)
            # Cuerpo redondeado
            self.canvas.create_oval(cx - 32, cy - 32 + bounce, cx + 32, cy + 32 + bounce,
                                    fill=body_color, outline="#F8FAFC", width=2)
            # Pancita suave
            self.canvas.create_oval(cx - 18, cy - 10 + bounce, cx + 18, cy + 24 + bounce,
                                    fill="#E0F2FE", outline="")
            # Ojos expresivos
            if self.state == "sleepy":
                # Ojos cerrados dormilones (líneas)
                self.canvas.create_line(cx - 20, cy - 8 + bounce, cx - 8, cy - 8 + bounce, fill="#0F172A", width=3)
                self.canvas.create_line(cx + 8, cy - 8 + bounce, cx + 20, cy - 8 + bounce, fill="#0F172A", width=3)
                # Burbujitas zZz flotando
                z_off = (f * 2) % 30
                self.canvas.create_text(cx + 36, cy - 25 - z_off, text="z", fill="#94A3B8", font=("Consolas", 10, "bold"))
                self.canvas.create_text(cx + 44, cy - 35 - z_off, text="Z", fill="#00F2FE", font=("Consolas", 13, "bold"))
            else:
                # Ojos brillantes
                eye_h = 4 if (f % 60 > 56) else 10  # Parpadeo natural
                self.canvas.create_oval(cx - 20, cy - 12 + bounce, cx - 8, cy - 12 + eye_h + bounce, fill="#0B0F19", outline="")
                self.canvas.create_oval(cx + 8, cy - 12 + bounce, cx + 20, cy - 12 + eye_h + bounce, fill="#0B0F19", outline="")
                # Brillo pupilar
                self.canvas.create_oval(cx - 17, cy - 11 + bounce, cx - 12, cy - 7 + bounce, fill="#FFFFFF", outline="")
                self.canvas.create_oval(cx + 11, cy - 11 + bounce, cx + 16, cy - 7 + bounce, fill="#FFFFFF", outline="")
                # Sonrisa
                if self.state == "hyper":
                    self.canvas.create_arc(cx - 10, cy - 2 + bounce, cx + 10, cy + 16 + bounce, start=0, extent=-180, fill="#FF0055", outline="#F8FAFC")
                else:
                    self.canvas.create_arc(cx - 8, cy + bounce, cx + 8, cy + 12 + bounce, start=0, extent=-180, fill="", outline="#0F172A", width=2)

            # Colita con rebote
            tail_x = cx - 34 + int(math.sin(f * 0.4) * 8)
            self.canvas.create_line(cx - 25, cy + 15 + bounce, tail_x, cy + 5 + bounce, fill=body_color, width=5, capstyle="round")

        else:
            # ── ETAPA 3+: MECHA CYBER DRAGON / ZORRO AVANZADO ──
            body_color = "#7B2CBF" if self.state != "hyper" else "#FF2A6D"
            # Alas cibernéticas
            wing_span = int(math.sin(f * 0.3) * 10)
            self.canvas.create_polygon([cx - 20, cy - 5 + bounce, cx - 55 - wing_span, cy - 35 + bounce, cx - 40, cy + 15 + bounce],
                                       fill="#00F2FE", outline="#F8FAFC", width=1.5)
            self.canvas.create_polygon([cx + 20, cy - 5 + bounce, cx + 55 + wing_span, cy - 35 + bounce, cx + 40, cy + 15 + bounce],
                                       fill="#00F2FE", outline="#F8FAFC", width=1.5)
            # Cabeza y Cuerpo Mecha
            self.canvas.create_oval(cx - 30, cy - 30 + bounce, cx + 30, cy + 30 + bounce,
                                    fill=body_color, outline="#05FFA1", width=2.5)
            # Visor Cyberpunk
            self.canvas.create_rectangle(cx - 22, cy - 12 + bounce, cx + 22, cy + 2 + bounce,
                                         fill="#05FFA1", outline="#F8FAFC", width=1.5)
            # Emoticon en visor
            visor_icon = "🔥" if self.state == "hyper" else ("⚡" if self.state == "cheering" else "^ _ ^")
            self.canvas.create_text(cx, cy - 5 + bounce, text=visor_icon, fill="#0B0F19", font=("Segoe UI", 9, "bold"))


# ── Aplicación Principal (Floating Widget & Health Hub) ─────────────────
class VitamonApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Configuración de Ventana Flotante
        self.title("Vitamon — Air5 Health Companion")
        self.geometry("380x680+1200+120")
        self.minsize(360, 480)
        self.attributes("-topmost", True)
        self.configure(fg_color=PALETTE["bg_dark"])

        # Estado de Arrastre de Ventana
        self.is_pinned = True
        self.is_compact = False
        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.do_drag)

        # Colas de comunicación con el Worker BLE
        self.data_queue = queue.Queue()
        self.cmd_queue = queue.Queue()

        # Almacén de Telemetría en Vivo
        self.metrics = {
            "hr": 78,
            "hr_history": [75, 78, 82, 80, 85, 90, 88, 84, 82, 79, 78],
            "spo2": 98,
            "steps": 2934,
            "calories": 210,
            "distance_m": 1850,
            "active_min": 35,
            "battery": 100,
            "status_text": "Iniciando conexión BLE...",
            "status_color": PALETTE["neon_amber"]
        }

        # Inicializar Componentes de UI
        self.setup_ui()

        # Iniciar Motor del Tamagotchi
        self.creature = VitamonCreature(self.canvas_creature)

        # Iniciar Hilo BLE
        self.ble_thread = BLEBridgeThread(self.data_queue, self.cmd_queue)
        self.ble_thread.start()

        # Loops de Actualización UI y Animación (50ms = 20fps)
        self.after(50, self.update_animation_loop)
        self.after(100, self.process_ble_queue_loop)

    def start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def do_drag(self, event):
        x = self.winfo_x() + (event.x - self._drag_x)
        y = self.winfo_y() + (event.y - self._drag_y)
        self.geometry(f"+{x}+{y}")

    def toggle_pin(self):
        self.is_pinned = not self.is_pinned
        self.attributes("-topmost", self.is_pinned)
        self.btn_pin.configure(text="📌 Fijado" if self.is_pinned else "📍 Libre",
                               fg_color=PALETTE["neon_teal"] if self.is_pinned else PALETTE["card_border"])

    def toggle_compact(self):
        self.is_compact = not self.is_compact
        if self.is_compact:
            self.geometry("380x330")
            self.frame_analytics.pack_forget()
            self.frame_controls.pack_forget()
            self.btn_compact.configure(text="🔍 Expandir")
        else:
            self.geometry("380x680")
            self.frame_analytics.pack(fill="x", padx=12, pady=6)
            self.frame_controls.pack(fill="x", padx=12, pady=6)
            self.btn_compact.configure(text="🗕 Mini")

    def setup_ui(self):
        # ── 1. Barra de Título y Controles de Ventana ──
        self.frame_top = ctk.CTkFrame(self, fg_color=PALETTE["card_bg"], corner_radius=12, height=44)
        self.frame_top.pack(fill="x", padx=10, pady=(10, 4))

        self.lbl_title = ctk.CTkLabel(self.frame_top, text="🐾 VITAMON", font=("Segoe UI", 14, "bold"), text_color=PALETTE["neon_teal"])
        self.lbl_title.pack(side="left", padx=12, pady=6)

        self.btn_compact = ctk.CTkButton(self.frame_top, text="🗕 Mini", width=55, height=26,
                                         fg_color=PALETTE["card_border"], text_color=PALETTE["text_main"],
                                         font=("Segoe UI", 10, "bold"), command=self.toggle_compact)
        self.btn_compact.pack(side="right", padx=(2, 6))

        self.btn_pin = ctk.CTkButton(self.frame_top, text="📌 Fijado", width=65, height=26,
                                     fg_color=PALETTE["neon_teal"], text_color="#0B0F19",
                                     font=("Segoe UI", 10, "bold"), command=self.toggle_pin)
        self.btn_pin.pack(side="right", padx=2)

        # ── 2. Tarjeta del Tamagotchi ──
        self.card_tamagotchi = ctk.CTkFrame(self, fg_color=PALETTE["card_bg"], corner_radius=16, border_width=1, border_color=PALETTE["card_border"])
        self.card_tamagotchi.pack(fill="x", padx=10, pady=4)

        # Canvas Animado
        self.canvas_creature = tk.Canvas(self.card_tamagotchi, width=220, height=140, bg=PALETTE["card_bg"], highlightthickness=0)
        self.canvas_creature.pack(pady=(8, 2))

        # Información de Nivel y Especie
        self.lbl_creature_name = ctk.CTkLabel(self.card_tamagotchi, text="Aero Sprite • Nivel 2", font=("Segoe UI", 13, "bold"), text_color=PALETTE["text_main"])
        self.lbl_creature_name.pack()

        # Barra de XP hacia la siguiente evolución
        self.frame_xp = ctk.CTkFrame(self.card_tamagotchi, fg_color="transparent")
        self.frame_xp.pack(fill="x", padx=20, pady=(2, 8))

        self.progress_xp = ctk.CTkProgressBar(self.frame_xp, height=8, progress_color=PALETTE["neon_teal"], fg_color=PALETTE["card_border"])
        self.progress_xp.pack(fill="x", pady=2)
        self.progress_xp.set(0.6)

        self.lbl_xp_text = ctk.CTkLabel(self.frame_xp, text="XP: 2,934 / 3,000 (¡97% para evolucionar!)", font=("Segoe UI", 10), text_color=PALETTE["text_muted"])
        self.lbl_xp_text.pack()

        # ── 3. Métricas en Tiempo Real (HUD de Salud) ──
        self.frame_hud = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_hud.pack(fill="x", padx=10, pady=4)

        # Grid de 2x2 cards
        self.frame_hud.columnconfigure(0, weight=1)
        self.frame_hud.columnconfigure(1, weight=1)

        # Card: Frecuencia Cardíaca
        self.card_hr = ctk.CTkFrame(self.frame_hud, fg_color=PALETTE["card_bg"], corner_radius=14, border_width=1, border_color=PALETTE["card_border"])
        self.card_hr.grid(row=0, column=0, padx=4, pady=4, sticky="nsew")

        self.lbl_hr_title = ctk.CTkLabel(self.card_hr, text="❤️ PULSO CARDÍACO", font=("Segoe UI", 10, "bold"), text_color=PALETTE["neon_rose"])
        self.lbl_hr_title.pack(anchor="w", padx=10, pady=(8, 0))

        self.lbl_hr_val = ctk.CTkLabel(self.card_hr, text="78 BPM", font=("Segoe UI", 20, "bold"), text_color=PALETTE["text_main"])
        self.lbl_hr_val.pack(anchor="w", padx=10, pady=(0, 2))

        self.lbl_hr_zone = ctk.CTkLabel(self.card_hr, text="Zona: Reposo Óptimo", font=("Segoe UI", 9), text_color=PALETTE["neon_green"])
        self.lbl_hr_zone.pack(anchor="w", padx=10, pady=(0, 8))

        # Card: Pasos y Meta
        self.card_steps = ctk.CTkFrame(self.frame_hud, fg_color=PALETTE["card_bg"], corner_radius=14, border_width=1, border_color=PALETTE["card_border"])
        self.card_steps.grid(row=0, column=1, padx=4, pady=4, sticky="nsew")

        self.lbl_steps_title = ctk.CTkLabel(self.card_steps, text="🚶 PASOS HOY", font=("Segoe UI", 10, "bold"), text_color=PALETTE["neon_teal"])
        self.lbl_steps_title.pack(anchor="w", padx=10, pady=(8, 0))

        self.lbl_steps_val = ctk.CTkLabel(self.card_steps, text="2,934", font=("Segoe UI", 20, "bold"), text_color=PALETTE["text_main"])
        self.lbl_steps_val.pack(anchor="w", padx=10, pady=(0, 2))

        self.lbl_steps_meta = ctk.CTkLabel(self.card_steps, text="Meta: 5,000 (58%)", font=("Segoe UI", 9), text_color=PALETTE["text_muted"])
        self.lbl_steps_meta.pack(anchor="w", padx=10, pady=(0, 8))

        # Card: SpO2 y Vitalidad
        self.card_spo2 = ctk.CTkFrame(self.frame_hud, fg_color=PALETTE["card_bg"], corner_radius=14, border_width=1, border_color=PALETTE["card_border"])
        self.card_spo2.grid(row=1, column=0, padx=4, pady=4, sticky="nsew")

        self.lbl_spo2_title = ctk.CTkLabel(self.card_spo2, text="🫁 OXÍGENO (SpO2)", font=("Segoe UI", 10, "bold"), text_color=PALETTE["neon_blue"])
        self.lbl_spo2_title.pack(anchor="w", padx=10, pady=(8, 0))

        self.lbl_spo2_val = ctk.CTkLabel(self.card_spo2, text="98 %", font=("Segoe UI", 18, "bold"), text_color=PALETTE["text_main"])
        self.lbl_spo2_val.pack(anchor="w", padx=10, pady=(0, 2))

        self.lbl_spo2_status = ctk.CTkLabel(self.card_spo2, text="Nivel Saludable", font=("Segoe UI", 9), text_color=PALETTE["neon_green"])
        self.lbl_spo2_status.pack(anchor="w", padx=10, pady=(0, 8))

        # Card: Batería y Calorías
        self.card_cal = ctk.CTkFrame(self.frame_hud, fg_color=PALETTE["card_bg"], corner_radius=14, border_width=1, border_color=PALETTE["card_border"])
        self.card_cal.grid(row=1, column=1, padx=4, pady=4, sticky="nsew")

        self.lbl_cal_title = ctk.CTkLabel(self.card_cal, text="🔥 ACTIVIDAD", font=("Segoe UI", 10, "bold"), text_color=PALETTE["neon_amber"])
        self.lbl_cal_title.pack(anchor="w", padx=10, pady=(8, 0))

        self.lbl_cal_val = ctk.CTkLabel(self.card_cal, text="210 kcal", font=("Segoe UI", 18, "bold"), text_color=PALETTE["text_main"])
        self.lbl_cal_val.pack(anchor="w", padx=10, pady=(0, 2))

        self.lbl_bat_status = ctk.CTkLabel(self.card_cal, text="🔋 Batería Reloj: 100%", font=("Segoe UI", 9), text_color=PALETTE["neon_green"])
        self.lbl_bat_status.pack(anchor="w", padx=10, pady=(0, 8))

        # ── 4. Motor de Predicciones y Estadísticas WOW ──
        self.frame_analytics = ctk.CTkFrame(self, fg_color=PALETTE["card_bg"], corner_radius=14, border_width=1, border_color=PALETTE["card_border"])
        self.frame_analytics.pack(fill="x", padx=10, pady=4)

        self.lbl_ana_title = ctk.CTkLabel(self.frame_analytics, text="🔮 ANALÍTICA & PREDICCIÓN INTELIGENTE", font=("Segoe UI", 10, "bold"), text_color=PALETTE["neon_teal"])
        self.lbl_ana_title.pack(anchor="w", padx=12, pady=(8, 4))

        # Predicción de Pasos a medianoche
        self.lbl_pred_steps = ctk.CTkLabel(self.frame_analytics, text="• Proyección 23:59: ~4,820 pasos (Ritmo estable)", font=("Segoe UI", 10), text_color=PALETTE["text_main"])
        self.lbl_pred_steps.pack(anchor="w", padx=12, pady=1)

        # Estrés estimado / HRV
        self.lbl_stress = ctk.CTkLabel(self.frame_analytics, text="• Índice de Estrés: 22/100 (Bajo / Relajado)", font=("Segoe UI", 10), text_color=PALETTE["neon_green"])
        self.lbl_stress.pack(anchor="w", padx=12, pady=1)

        # Batería Corporal
        self.lbl_body_bat = ctk.CTkLabel(self.frame_analytics, text="• Batería Corporal: 85% (Alta energía para trabajar)", font=("Segoe UI", 10), text_color=PALETTE["neon_teal"])
        self.lbl_body_bat.pack(anchor="w", padx=12, pady=(1, 8))

        # ── 5. Barra de Control de Smartwatch ──
        self.frame_controls = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_controls.pack(fill="x", padx=10, pady=(4, 8))

        # Botón Anti-Spam Sedentario
        self.btn_anti_spam = ctk.CTkButton(self.frame_controls, text="🔕 Silenciar Spam", height=28,
                                           fg_color=PALETTE["card_bg"], border_width=1, border_color=PALETTE["neon_rose"],
                                           text_color=PALETTE["neon_rose"], font=("Segoe UI", 10, "bold"),
                                           command=self.action_silence_sedentary)
        self.btn_anti_spam.pack(side="left", fill="x", expand=True, padx=(0, 2))

        # Botón Sincronizar Hora
        self.btn_sync_time = ctk.CTkButton(self.frame_controls, text="⏰ Sincronizar Hora", height=28,
                                           fg_color=PALETTE["card_bg"], border_width=1, border_color=PALETTE["neon_teal"],
                                           text_color=PALETTE["neon_teal"], font=("Segoe UI", 10, "bold"),
                                           command=self.action_sync_time)
        self.btn_sync_time.pack(side="left", fill="x", expand=True, padx=2)

        # Botón Buscar / Vibrar
        self.btn_vibrate = ctk.CTkButton(self.frame_controls, text="📳 Vibrar", height=28,
                                         fg_color=PALETTE["card_bg"], border_width=1, border_color=PALETTE["neon_amber"],
                                         text_color=PALETTE["neon_amber"], font=("Segoe UI", 10, "bold"),
                                         command=self.action_vibrate)
        self.btn_vibrate.pack(side="left", fill="x", expand=True, padx=(2, 0))

        # ── 6. Barra de Estado BLE Inferior ──
        self.lbl_status = ctk.CTkLabel(self, text="🟢 Air5 Conectado en Tiempo Real", font=("Segoe UI", 9), text_color=PALETTE["neon_green"])
        self.lbl_status.pack(side="bottom", pady=4)

    # ── Acciones de Usuario ──
    def action_silence_sedentary(self):
        self.cmd_queue.put({"action": "silence_sedentary"})
        messagebox.showinfo("Anti-Spam", "Comando enviado: El aviso de inactividad ha sido silenciado (intervalo 255min).")

    def action_sync_time(self):
        self.cmd_queue.put({"action": "sync_time"})
        messagebox.showinfo("Sincronización", f"Hora del reloj sincronizada con la PC: {datetime.now().strftime('%H:%M:%S')}")

    def action_vibrate(self):
        self.cmd_queue.put({"action": "vibrate"})

    # ── Loop de Procesamiento de Mensajes BLE ──
    def process_ble_queue_loop(self):
        try:
            while not self.data_queue.empty():
                msg = self.data_queue.get_nowait()
                m_type = msg.get("type")

                if m_type == "status":
                    status = msg.get("status")
                    text = msg.get("msg")
                    if status == "connected":
                        self.lbl_status.configure(text=f"🟢 {text}", text_color=PALETTE["neon_green"])
                    elif status == "connecting":
                        self.lbl_status.configure(text=f"🟡 {text}", text_color=PALETTE["neon_amber"])
                    else:
                        self.lbl_status.configure(text=f"🔴 {text}", text_color=PALETTE["neon_rose"])

                elif m_type == "live_hr":
                    bpm = msg.get("value")
                    self.metrics["hr"] = bpm
                    self.metrics["hr_history"].append(bpm)
                    if len(self.metrics["hr_history"]) > 30:
                        self.metrics["hr_history"].pop(0)

                    self.lbl_hr_val.configure(text=f"{bpm} BPM")
                    if bpm < 65:
                        self.lbl_hr_zone.configure(text="Zona: Reposo Profundo", text_color=PALETTE["neon_blue"])
                    elif bpm < 95:
                        self.lbl_hr_zone.configure(text="Zona: Ligera / Óptima", text_color=PALETTE["neon_green"])
                    elif bpm < 130:
                        self.lbl_hr_zone.configure(text="Zona: Quema Aeróbica", text_color=PALETTE["neon_amber"])
                    else:
                        self.lbl_hr_zone.configure(text="Zona: Alta Intensidad 🔥", text_color=PALETTE["neon_rose"])

                elif m_type == "live_spo2":
                    val = msg.get("value")
                    self.metrics["spo2"] = val
                    self.lbl_spo2_val.configure(text=f"{val} %")
                    status_str = "Excelente" if val >= 97 else ("Normal" if val >= 94 else "Atención")
                    color = PALETTE["neon_green"] if val >= 94 else PALETTE["neon_amber"]
                    self.lbl_spo2_status.configure(text=status_str, text_color=color)

                elif m_type == "daily_activity":
                    steps = msg.get("steps")
                    cal = msg.get("calories")
                    dist = msg.get("distance")
                    active = msg.get("active_min")
                    self.metrics["steps"] = steps
                    self.metrics["calories"] = cal
                    self.metrics["distance_m"] = dist
                    self.metrics["active_min"] = active

                    self.lbl_steps_val.configure(text=f"{steps:,}")
                    pct = min(100, int((steps / 5000) * 100))
                    self.lbl_steps_meta.configure(text=f"Meta: 5,000 ({pct}%)")
                    self.lbl_cal_val.configure(text=f"{cal} kcal")

                elif m_type == "battery":
                    pct = msg.get("value")
                    self.metrics["battery"] = pct
                    self.lbl_bat_status.configure(text=f"🔋 Batería Reloj: {pct}%")

        except queue.Empty:
            pass

        self.after(100, self.process_ble_queue_loop)

    # ── Loop de Render y Animación del Tamagotchi ──
    def update_animation_loop(self):
        # 1. Actualizar estado y render de la criatura
        self.creature.update_stats(self.metrics["steps"], self.metrics["hr"], self.metrics["active_min"])
        self.creature.render()

        # 2. Actualizar textos de nivel y XP
        stage_name = self.creature.get_stage_name()
        self.lbl_creature_name.configure(text=f"{stage_name} • Nivel {self.creature.level}")

        cur_xp_in_level = self.metrics["steps"] % 500
        progress_val = cur_xp_in_level / 500.0
        self.progress_xp.set(progress_val)
        self.lbl_xp_text.configure(text=f"XP: {self.metrics['steps']:,} / {self.creature.xp_to_next:,} ({int(progress_val*100)}% a Nivel {self.creature.level+1})")

        # 3. Calcular Predicciones Inteligentes (cada ~1 segundo)
        if self.creature.frame % 20 == 0:
            now = datetime.now()
            hours_passed = max(1, now.hour + now.minute / 60.0)
            hours_left = max(0, 24 - hours_passed)
            step_rate = self.metrics["steps"] / hours_passed
            predicted_total = int(self.metrics["steps"] + (step_rate * hours_left))
            self.lbl_pred_steps.configure(text=f"• Proyección 23:59: ~{predicted_total:,} pasos ({'+' if predicted_total>=5000 else '-'} Meta)")

            # Estrés estimado por variabilidad de pulso
            if len(self.metrics["hr_history"]) >= 5:
                diffs = [abs(self.metrics["hr_history"][i] - self.metrics["hr_history"][i-1]) for i in range(1, len(self.metrics["hr_history"]))]
                avg_diff = sum(diffs) / len(diffs)
                stress_score = max(5, min(95, int(100 - (avg_diff * 12) + (self.metrics["hr"] - 70))))
                stress_label = "Bajo / Relajado" if stress_score < 35 else ("Moderado" if stress_score < 65 else "Elevado")
                color = PALETTE["neon_green"] if stress_score < 35 else (PALETTE["neon_amber"] if stress_score < 65 else PALETTE["neon_rose"])
                self.lbl_stress.configure(text=f"• Índice de Estrés: {stress_score}/100 ({stress_label})", text_color=color)

        self.after(50, self.update_animation_loop)


if __name__ == "__main__":
    app = VitamonApp()
    app.mainloop()
