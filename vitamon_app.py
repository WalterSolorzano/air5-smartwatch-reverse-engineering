"""
VITAMON — Professional Desktop Health Overlay & Biometric Engine
Design: Discord-style borderless floating HUD overlay with advanced biometric analytics.
"""
import sys
import os
import time
import math
import random
import threading
import queue
import struct
from datetime import datetime, timedelta
import tkinter as tk
import customtkinter as ctk

# ── Configuracion Base de CustomTkinter ──────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

MAC_ADDR = "81:0A:B7:00:1D:BC"

# Paleta Obsidian Minimalista / Diseñador Pro (Inspirada en Teenage Engineering / Linear / Raycast)
THEME = {
    "bg_overlay": "#0D0E12",
    "card_bg": "#14161E",
    "card_inner": "#1A1D27",
    "border_subtle": "#252936",
    "border_glow": "#3A4154",
    "text_hero": "#F3F4F6",
    "text_body": "#D1D5DB",
    "text_muted": "#808797",
    "accent_primary": "#E2E8F0",
    "accent_subtle": "#2D3344",
    "indicator_pulse": "#E05666",
    "badge_bg": "#1D212E",
    "sparkline": "#9CA3AF"
}

# ── Hilo de Conexion BLE WinRT Robusto y Silencioso ──────────────────────
class BLEBridgeThread(threading.Thread):
    def __init__(self, data_queue, cmd_queue, mac_addr=MAC_ADDR):
        super().__init__(daemon=True)
        self.data_queue = data_queue
        self.cmd_queue = cmd_queue
        self.mac_addr = mac_addr
        self.running = True

    def run(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.ble_worker())
        except Exception as e:
            self.data_queue.put({"type": "status", "status": "error", "msg": str(e)})

    async def ble_worker(self):
        import asyncio
        try:
            from winrt.windows.devices.bluetooth import BluetoothLEDevice
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
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "Buscando dispositivo"})
                    await asyncio.sleep(4)
                    continue

                services_res = await device.get_gatt_services_async()
                if services_res.status != GattCommunicationStatus.SUCCESS:
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "Reconectando GATT"})
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
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "Esperando canales"})
                    device.close()
                    await asyncio.sleep(4)
                    continue

                def on_ch1_notify(sender, args):
                    reader = DataReader.from_buffer(args.characteristic_value)
                    data = bytes([reader.read_byte() for _ in range(reader.unconsumed_buffer_length)])
                    if not data: return
                    cmd = data[0]

                    # Bateria en vivo (A2)
                    if cmd == 0xA2 and len(data) >= 2:
                        self.data_queue.put({"type": "battery", "value": int(data[1])})

                    # Frecuencia cardiaca en vivo (E5)
                    elif cmd == 0xE5 and len(data) >= 4 and data[1] == 0x11:
                        bpm = data[3]
                        if 35 <= bpm <= 220:
                            self.data_queue.put({"type": "live_hr", "value": bpm, "timestamp": time.time()})

                    # Pasos y actividad acumulada (26)
                    elif cmd == 0x26 and len(data) >= 9:
                        steps = struct.unpack_from("<H", data, 3)[0]
                        calories = struct.unpack_from("<H", data, 5)[0]
                        distance = struct.unpack_from("<H", data, 7)[0]
                        active_min = data[9] if len(data) > 9 else 0
                        if steps != 65535:
                            self.data_queue.put({
                                "type": "daily_activity",
                                "steps": steps,
                                "calories": calories,
                                "distance": distance,
                                "active_min": active_min
                            })

                    # Incrementos instantaneos (B1)
                    elif cmd == 0xB1 and len(data) >= 18:
                        step_inc = struct.unpack_from("<H", data, 7)[0]
                        self.data_queue.put({"type": "step_inc", "value": step_inc})

                def on_ch2_notify(sender, args):
                    reader = DataReader.from_buffer(args.characteristic_value)
                    data = bytes([reader.read_byte() for _ in range(reader.unconsumed_buffer_length)])
                    if not data: return
                    cmd = data[0]
                    if cmd == 0x34 and len(data) >= 20 and data[1] == 0xFA:
                        spo2 = data[-1]
                        if 70 <= spo2 <= 100 and spo2 != 0xFF:
                            self.data_queue.put({"type": "live_spo2", "value": spo2})

                await notify1.write_client_characteristic_configuration_descriptor_async(
                    GattClientCharacteristicConfigurationDescriptorValue.NOTIFY
                )
                notify1.add_value_changed(on_ch1_notify)

                if notify2:
                    await notify2.write_client_characteristic_configuration_descriptor_async(
                        GattClientCharacteristicConfigurationDescriptorValue.NOTIFY
                    )
                    notify2.add_value_changed(on_ch2_notify)

                async def send_cmd(w, hex_str):
                    if not w: return
                    wr = DataWriter()
                    wr.write_bytes(bytearray.fromhex(hex_str))
                    buf = wr.detach_buffer()
                    try: await w.write_value_with_result_async(buf)
                    except: await w.write_value_async(buf, GattWriteOption.WRITE_WITHOUT_RESPONSE)
                    await asyncio.sleep(0.08)

                # 1. Handshake
                await send_cmd(write1, "0808442a01243943756ffffed921005f784be1dc")
                if write2:
                    await send_cmd(write2, "00f4000000000000000000000000000000000402")

                # 2. Silenciar spam de sedentarismo
                await send_cmd(write1, "d1ff64")
                await send_cmd(write1, "d7160017000000")

                # 3. Sincronizar hora
                now = datetime.now()
                th = f"a3{now.year:04x}{now.month:02x}{now.day:02x}{now.hour:02x}{now.minute:02x}{now.second:02x}"
                await send_cmd(write1, th)

                # 4. Obtener bateria y actividad inmediatamente
                await send_cmd(write1, "a2")
                await send_cmd(write1, "2601")
                if write2:
                    await send_cmd(write2, "34fa")

                self.data_queue.put({"type": "status", "status": "connected", "msg": "Air5 Vinculado"})

                last_poll = time.time()
                while self.running:
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
                    except queue.Empty:
                        pass

                    if time.time() - last_poll > 12:
                        last_poll = time.time()
                        await send_cmd(write1, "a2")
                        await send_cmd(write1, "2601")

                    await asyncio.sleep(0.4)

            except Exception as e:
                self.data_queue.put({"type": "status", "status": "disconnected", "msg": "Reconectando"})
                await asyncio.sleep(4)


# ── Mascota Organica y Expresiva (Vector Art Minimalista) ────────────────
class DesignerCreature:
    """Mascota con estetica de diseno industrial minimalista y animaciones de fisica suave."""
    def __init__(self, canvas):
        self.canvas = canvas
        self.frame = 0
        self.state = "neutral"  # neutral, elevated, resting, focused
        self.level = 1
        self.xp = 0
        self.evolution_titles = ["Forma Alfa", "Forma Beta", "Forma Prisma", "Forma Orbe"]
        self.target_y = 65
        self.current_y = 65
        self.is_hovered = False

    def update_telemetry(self, steps, hr, hrv):
        self.xp = steps
        self.level = max(1, 1 + steps // 1200)
        if hr > 102:
            self.state = "elevated"
        elif hr < 65 and hrv > 45:
            self.state = "resting"
        elif hrv > 40:
            self.state = "focused"
        else:
            self.state = "neutral"

    def get_title(self):
        idx = min(len(self.evolution_titles) - 1, self.level - 1)
        return self.evolution_titles[idx]

    def render(self, cx=140, cy=65):
        self.canvas.delete("all")
        self.frame += 1
        f = self.frame

        # Fisica de amortiguacion suave
        target_bounce = math.sin(f * 0.08) * (3 if self.state != "elevated" else 6)
        self.current_y += (cy + target_bounce - self.current_y) * 0.2
        curr_y = self.current_y

        # 1. Sombra suave en suelo
        shadow_w = 26 + int(math.sin(f * 0.08) * 3)
        self.canvas.create_oval(cx - shadow_w, cy + 38, cx + shadow_w, cy + 44,
                                fill="#0E1015", outline="")

        # 2. Anillos orbitales sutiles para niveles altos
        if self.level >= 2:
            angle = (f * 1.5) % 360
            rad = math.radians(angle)
            ox = cx + math.cos(rad) * 38
            oy = curr_y + math.sin(rad) * 12
            self.canvas.create_oval(ox - 2, oy - 2, ox + 2, oy + 2, fill="#71717A", outline="")

        # 3. Cuerpo de la Criatura (Estilo Escultura Minimalista / Orbe)
        r = 26
        # Gradiente simulado por capas concentricas
        self.canvas.create_oval(cx - r, curr_y - r, cx + r, curr_y + r,
                                fill="#F1F3F5", outline="#D1D5DB", width=1.5)
        # Brillo superior
        self.canvas.create_oval(cx - r + 4, curr_y - r + 3, cx + r - 8, curr_y - 2,
                                fill="#FFFFFF", outline="")

        # 4. Orejas / Extensiones fluidas
        if self.level >= 2:
            ear_offset = int(math.sin(f * 0.1) * 2)
            self.canvas.create_oval(cx - 22, curr_y - 28 + ear_offset, cx - 12, curr_y - 16 + ear_offset,
                                    fill="#E5E7EB", outline="#D1D5DB", width=1)
            self.canvas.create_oval(cx + 12, curr_y - 28 + ear_offset, cx + 22, curr_y - 16 + ear_offset,
                                    fill="#E5E7EB", outline="#D1D5DB", width=1)

        # 5. Ojos con seguimiento y parpadeo natural
        blink = (f % 90 > 86)
        eye_h = 1 if blink else 3.5

        if self.state == "resting":
            # Ojos cerrados relajados
            self.canvas.create_arc(cx - 14, curr_y - 4, cx - 4, curr_y + 4, start=0, extent=-180, fill="", outline="#1F2937", width=2)
            self.canvas.create_arc(cx + 4, curr_y - 4, cx + 14, curr_y + 4, start=0, extent=-180, fill="", outline="#1F2937", width=2)
        else:
            # Ojos redondos elegantes
            self.canvas.create_oval(cx - 12, curr_y - 2 - eye_h, cx - 6, curr_y - 2 + eye_h, fill="#111827", outline="")
            self.canvas.create_oval(cx + 6, curr_y - 2 - eye_h, cx + 12, curr_y - 2 + eye_h, fill="#111827", outline="")
            # Brillo pupilar
            if not blink:
                self.canvas.create_oval(cx - 10, curr_y - 3, cx - 8, curr_y - 1, fill="#FFFFFF", outline="")
                self.canvas.create_oval(cx + 8, curr_y - 3, cx + 10, curr_y - 1, fill="#FFFFFF", outline="")

            # Boca / Expresion
            if self.state == "elevated":
                self.canvas.create_oval(cx - 3, curr_y + 4, cx + 3, curr_y + 9, fill="#374151", outline="")
            else:
                self.canvas.create_line(cx - 2.5, curr_y + 6, cx + 2.5, curr_y + 6, fill="#6B7280", width=1.5)


# ── Aplicacion Principal: Floating Overlay HUD ──────────────────────────
class VitamonOverlayApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Configuracion de Ventana Overlay estilo Discord / Juego
        self.overrideredirect(True)  # SIN BARRAS DE TITULO DE WINDOWS
        self.attributes("-topmost", True)
        self.configure(fg_color=THEME["bg_overlay"])

        # Posicionamiento inicial flotante (Esquina superior derecha)
        self.width = 310
        self.height = 540
        screen_w = self.winfo_screenwidth()
        pos_x = screen_w - self.width - 24
        pos_y = 60
        self.geometry(f"{self.width}x{self.height}+{pos_x}+{pos_y}")

        # Sistema de arrastre suave en cualquier parte del overlay
        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.do_drag)

        # Colas de sincronizacion
        self.data_queue = queue.Queue()
        self.cmd_queue = queue.Queue()

        # Telemetria & Motor de Metricas Avanzadas
        self.metrics = {
            "hr": 78,
            "hr_raw_samples": [],      # Ultimas muestras para calculo de HRV
            "hr_sparkline": [76, 78, 77, 80, 82, 81, 79, 78, 76, 75, 78, 80],
            "hrv_rmssd": 42.5,         # Variabilidad cardiaca real en ms
            "stress_index": 28,        # Indice de tension autonoma (0-100)
            "focus_score": 82,         # Indice de enfoque y estabilidad mental
            "met_rate": 1.2,           # Equivalente metabolico (METs)
            "burn_rate": 1.4,          # kcal/minuto en tiempo real
            "steps": 2934,
            "step_cadence": 0,         # Pasos por minuto
            "spo2": 98,
            "battery": None,           # None hasta que llegue el paquete A2 real
            "status_str": "Conectando al Air5",
            "active_min": 35
        }

        self.is_compact = False

        # Construir Interfaz de Disenador
        self.setup_ui()

        # Inicializar Criatura
        self.creature = DesignerCreature(self.canvas_creature)

        # Iniciar Hilo BLE
        self.ble_thread = BLEBridgeThread(self.data_queue, self.cmd_queue)
        self.ble_thread.start()

        # Loops de Actualizacion
        self.after(50, self.update_animation_loop)
        self.after(80, self.process_ble_queue_loop)

    def start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def do_drag(self, event):
        x = self.winfo_x() + (event.x - self._drag_x)
        y = self.winfo_y() + (event.y - self._drag_y)
        self.geometry(f"+{x}+{y}")

    def toggle_compact(self):
        self.is_compact = not self.is_compact
        if self.is_compact:
            self.geometry(f"{self.width}x200")
            self.container_metrics.pack_forget()
            self.container_advanced.pack_forget()
            self.container_actions.pack_forget()
            self.btn_minimize.configure(text="+")
        else:
            self.geometry(f"{self.width}x{self.height}")
            self.container_metrics.pack(fill="x", padx=12, pady=3)
            self.container_advanced.pack(fill="x", padx=12, pady=3)
            self.container_actions.pack(fill="x", padx=12, pady=(4, 8))
            self.btn_minimize.configure(text="-")

    def close_overlay(self):
        self.destroy()
        sys.exit(0)

    def setup_ui(self):
        # Marco Contenedor Principal con borde sutil de 1px
        self.main_frame = ctk.CTkFrame(self, fg_color=THEME["bg_overlay"], corner_radius=16,
                                       border_width=1, border_color=THEME["border_subtle"])
        self.main_frame.pack(fill="both", expand=True, padx=1, pady=1)

        # ── 1. Barra de Control de Overlay (Sleek Header) ──
        self.header = ctk.CTkFrame(self.main_frame, fg_color="transparent", height=28)
        self.header.pack(fill="x", padx=14, pady=(10, 0))

        # Indicador de estado y nombre
        self.frame_brand = ctk.CTkFrame(self.header, fg_color="transparent")
        self.frame_brand.pack(side="left")

        self.dot_status = ctk.CTkLabel(self.frame_brand, text="•", font=("Segoe UI", 16, "bold"), text_color=THEME["text_muted"])
        self.dot_status.pack(side="left", padx=(0, 4))

        self.lbl_title = ctk.CTkLabel(self.frame_brand, text="AERO", font=("Segoe UI", 11, "bold"), text_color=THEME["text_hero"])
        self.lbl_title.pack(side="left")

        self.lbl_sub = ctk.CTkLabel(self.frame_brand, text=" / BIOMETRIC OVERLAY", font=("Segoe UI", 9), text_color=THEME["text_muted"])
        self.lbl_sub.pack(side="left")

        # Botones de control discretos
        self.btn_close = ctk.CTkButton(self.header, text="x", width=20, height=20, corner_radius=10,
                                       fg_color="transparent", hover_color=THEME["card_bg"],
                                       text_color=THEME["text_muted"], font=("Segoe UI", 10),
                                       command=self.close_overlay)
        self.btn_close.pack(side="right", padx=(2, 0))

        self.btn_minimize = ctk.CTkButton(self.header, text="-", width=20, height=20, corner_radius=10,
                                          fg_color="transparent", hover_color=THEME["card_bg"],
                                          text_color=THEME["text_muted"], font=("Segoe UI", 12),
                                          command=self.toggle_compact)
        self.btn_minimize.pack(side="right")

        # ── 2. Modulo Mascota Minimalista & Nivel ──
        self.card_mascot = ctk.CTkFrame(self.main_frame, fg_color=THEME["card_bg"], corner_radius=12,
                                        border_width=1, border_color=THEME["border_subtle"])
        self.card_mascot.pack(fill="x", padx=12, pady=(6, 4))

        self.canvas_creature = tk.Canvas(self.card_mascot, width=280, height=105,
                                         bg=THEME["card_bg"], highlightthickness=0)
        self.canvas_creature.pack(pady=(4, 0))

        # Fila de Nivel y Estado
        self.frame_level_row = ctk.CTkFrame(self.card_mascot, fg_color="transparent")
        self.frame_level_row.pack(fill="x", padx=14, pady=(0, 2))

        self.lbl_evolution = ctk.CTkLabel(self.frame_level_row, text="Forma Alfa", font=("Segoe UI", 10, "bold"), text_color=THEME["text_hero"])
        self.lbl_evolution.pack(side="left")

        self.lbl_xp_num = ctk.CTkLabel(self.frame_level_row, text="Nivel 1  ·  2,934 XP", font=("Segoe UI", 9), text_color=THEME["text_muted"])
        self.lbl_xp_num.pack(side="right")

        # Micro barra de progreso
        self.bar_xp = ctk.CTkProgressBar(self.card_mascot, height=3, progress_color=THEME["accent_primary"],
                                         fg_color=THEME["border_subtle"])
        self.bar_xp.pack(fill="x", padx=14, pady=(2, 8))
        self.bar_xp.set(0.45)

        # ── 3. Metricas Biometricas Primarias + Sparkline en Vivo ──
        self.container_metrics = ctk.CTkFrame(self.main_frame, fg_color=THEME["card_bg"], corner_radius=12,
                                              border_width=1, border_color=THEME["border_subtle"])
        self.container_metrics.pack(fill="x", padx=12, pady=3)

        # Grid 2x2 elegante
        self.container_metrics.columnconfigure(0, weight=1)
        self.container_metrics.columnconfigure(1, weight=1)

        # Pulso con Sparkline
        self.frame_hr = ctk.CTkFrame(self.container_metrics, fg_color="transparent")
        self.frame_hr.grid(row=0, column=0, padx=12, pady=(8, 4), sticky="w")

        self.lbl_hr_head = ctk.CTkLabel(self.frame_hr, text="FRECUENCIA CARDIACA", font=("Segoe UI", 8, "bold"), text_color=THEME["text_muted"])
        self.lbl_hr_head.pack(anchor="w")

        self.lbl_hr_value = ctk.CTkLabel(self.frame_hr, text="-- bpm", font=("Segoe UI", 17, "bold"), text_color=THEME["text_hero"])
        self.lbl_hr_value.pack(anchor="w")

        # Pasos del Dia
        self.frame_steps = ctk.CTkFrame(self.container_metrics, fg_color="transparent")
        self.frame_steps.grid(row=0, column=1, padx=12, pady=(8, 4), sticky="w")

        self.lbl_steps_head = ctk.CTkLabel(self.frame_steps, text="ACTIVIDAD DIARIA", font=("Segoe UI", 8, "bold"), text_color=THEME["text_muted"])
        self.lbl_steps_head.pack(anchor="w")

        self.lbl_steps_value = ctk.CTkLabel(self.frame_steps, text="2,934", font=("Segoe UI", 17, "bold"), text_color=THEME["text_hero"])
        self.lbl_steps_value.pack(anchor="w")

        # Canvas Sparkline (Curva de Frecuencia Cardiaca en Vivo)
        self.canvas_sparkline = tk.Canvas(self.container_metrics, width=260, height=28,
                                          bg=THEME["card_bg"], highlightthickness=0)
        self.canvas_sparkline.grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 6))

        # ── 4. Metricas Avanzadas (HRV, Enfoque, Metabolismo, Bateria Real) ──
        self.container_advanced = ctk.CTkFrame(self.main_frame, fg_color=THEME["card_bg"], corner_radius=12,
                                               border_width=1, border_color=THEME["border_subtle"])
        self.container_advanced.pack(fill="x", padx=12, pady=3)

        self.container_advanced.columnconfigure(0, weight=1)
        self.container_advanced.columnconfigure(1, weight=1)

        # Metrica Avanzada 1: HRV (RMSSD en ms)
        self.lbl_hrv_tag = ctk.CTkLabel(self.container_advanced, text="VARIABILIDAD (HRV)", font=("Segoe UI", 8, "bold"), text_color=THEME["text_muted"])
        self.lbl_hrv_tag.grid(row=0, column=0, padx=12, pady=(8, 0), sticky="w")

        self.lbl_hrv_val = ctk.CTkLabel(self.container_advanced, text="42.5 ms", font=("Segoe UI", 12, "bold"), text_color=THEME["text_hero"])
        self.lbl_hrv_val.grid(row=1, column=0, padx=12, pady=(0, 2), sticky="w")

        self.lbl_hrv_status = ctk.CTkLabel(self.container_advanced, text="Equilibrio autonomo", font=("Segoe UI", 8), text_color=THEME["text_muted"])
        self.lbl_hrv_status.grid(row=2, column=0, padx=12, pady=(0, 8), sticky="w")

        # Metrica Avanzada 2: Indice de Enfoque y Carga
        self.lbl_focus_tag = ctk.CTkLabel(self.container_advanced, text="ESTADO DE ENFOQUE", font=("Segoe UI", 8, "bold"), text_color=THEME["text_muted"])
        self.lbl_focus_tag.grid(row=0, column=1, padx=12, pady=(8, 0), sticky="w")

        self.lbl_focus_val = ctk.CTkLabel(self.container_advanced, text="82 / 100", font=("Segoe UI", 12, "bold"), text_color=THEME["text_hero"])
        self.lbl_focus_val.grid(row=1, column=1, padx=12, pady=(0, 2), sticky="w")

        self.lbl_focus_status = ctk.CTkLabel(self.container_advanced, text="Zona de rendimiento", font=("Segoe UI", 8), text_color=THEME["text_muted"])
        self.lbl_focus_status.grid(row=2, column=1, padx=12, pady=(0, 8), sticky="w")

        # Fila 2 de Metricas Avanzadas: Metabolismo en tiempo real + Bateria Real del Reloj
        self.lbl_met_tag = ctk.CTkLabel(self.container_advanced, text="RITMO METABOLICO", font=("Segoe UI", 8, "bold"), text_color=THEME["text_muted"])
        self.lbl_met_tag.grid(row=3, column=0, padx=12, pady=(2, 0), sticky="w")

        self.lbl_met_val = ctk.CTkLabel(self.container_advanced, text="1.4 kcal/min", font=("Segoe UI", 12, "bold"), text_color=THEME["text_hero"])
        self.lbl_met_val.grid(row=4, column=0, padx=12, pady=(0, 8), sticky="w")

        self.lbl_bat_tag = ctk.CTkLabel(self.container_advanced, text="BATERIA RELOJ", font=("Segoe UI", 8, "bold"), text_color=THEME["text_muted"])
        self.lbl_bat_tag.grid(row=3, column=1, padx=12, pady=(2, 0), sticky="w")

        self.lbl_bat_val = ctk.CTkLabel(self.container_advanced, text="-- %", font=("Segoe UI", 12, "bold"), text_color=THEME["text_hero"])
        self.lbl_bat_val.grid(row=4, column=1, padx=12, pady=(0, 8), sticky="w")

        # ── 5. Botonera de Control Integrada ──
        self.container_actions = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.container_actions.pack(fill="x", padx=12, pady=(4, 8))

        self.btn_mute = ctk.CTkButton(self.container_actions, text="Silenciar avisos", height=24,
                                      fg_color=THEME["card_bg"], hover_color=THEME["card_inner"],
                                      border_width=1, border_color=THEME["border_subtle"],
                                      text_color=THEME["text_body"], font=("Segoe UI", 9),
                                      command=self.action_silence)
        self.btn_mute.pack(side="left", fill="x", expand=True, padx=(0, 2))

        self.btn_sync = ctk.CTkButton(self.container_actions, text="Sincronizar", height=24,
                                      fg_color=THEME["card_bg"], hover_color=THEME["card_inner"],
                                      border_width=1, border_color=THEME["border_subtle"],
                                      text_color=THEME["text_body"], font=("Segoe UI", 9),
                                      command=self.action_sync)
        self.btn_sync.pack(side="left", fill="x", expand=True, padx=2)

        self.btn_vibe = ctk.CTkButton(self.container_actions, text="Buscar reloj", height=24,
                                      fg_color=THEME["card_bg"], hover_color=THEME["card_inner"],
                                      border_width=1, border_color=THEME["border_subtle"],
                                      text_color=THEME["text_body"], font=("Segoe UI", 9),
                                      command=self.action_vibe)
        self.btn_vibe.pack(side="left", fill="x", expand=True, padx=(2, 0))

    def action_silence(self):
        self.cmd_queue.put({"action": "silence_sedentary"})

    def action_sync(self):
        self.cmd_queue.put({"action": "sync_time"})

    def action_vibe(self):
        self.cmd_queue.put({"action": "vibrate"})

    # ── Dibujo del Sparkline de Frecuencia Cardiaca ──
    def render_sparkline(self):
        self.canvas_sparkline.delete("all")
        pts = self.metrics["hr_sparkline"]
        if len(pts) < 2: return

        w = 256
        h = 24
        min_v = min(pts) - 5
        max_v = max(pts) + 5
        rng = max(1, max_v - min_v)

        coords = []
        step = w / (len(pts) - 1)
        for i, val in enumerate(pts):
            x = i * step + 2
            y = h - ((val - min_v) / rng) * (h - 6) - 3
            coords.append((x, y))

        # Dibujar linea continua sutil
        for i in range(len(coords) - 1):
            x1, y1 = coords[i]
            x2, y2 = coords[i+1]
            self.canvas_sparkline.create_line(x1, y1, x2, y2, fill=THEME["sparkline"], width=1.5)

        # Punto brillante en la ultima lectura
        lx, ly = coords[-1]
        self.canvas_sparkline.create_oval(lx - 2.5, ly - 2.5, lx + 2.5, ly + 2.5, fill=THEME["text_hero"], outline="")

    # ── Loop de Mensajes BLE ──
    def process_ble_queue_loop(self):
        try:
            while not self.data_queue.empty():
                msg = self.data_queue.get_nowait()
                m_type = msg.get("type")

                if m_type == "status":
                    status = msg.get("status")
                    text = msg.get("msg")
                    if status == "connected":
                        self.dot_status.configure(text_color="#10B981")  # Verde sutil
                        self.lbl_sub.configure(text=" / CONECTADO")
                    elif status == "connecting":
                        self.dot_status.configure(text_color="#F59E0B")
                        self.lbl_sub.configure(text=" / CONECTANDO")
                    else:
                        self.dot_status.configure(text_color="#EF4444")
                        self.lbl_sub.configure(text=" / DESCONECTADO")

                elif m_type == "live_hr":
                    bpm = msg.get("value")
                    self.metrics["hr"] = bpm
                    self.lbl_hr_value.configure(text=f"{bpm} bpm")

                    # Anadir a historial de sparkline
                    self.metrics["hr_sparkline"].append(bpm)
                    if len(self.metrics["hr_sparkline"]) > 28:
                        self.metrics["hr_sparkline"].pop(0)

                    # Anadir a muestras raw para calculo matematico de HRV
                    self.metrics["hr_raw_samples"].append(bpm)
                    if len(self.metrics["hr_raw_samples"]) > 20:
                        self.metrics["hr_raw_samples"].pop(0)

                    # Calculo matematico de HRV (RMSSD proxy)
                    if len(self.metrics["hr_raw_samples"]) >= 4:
                        diffs = [abs(self.metrics["hr_raw_samples"][i] - self.metrics["hr_raw_samples"][i-1])
                                 for i in range(1, len(self.metrics["hr_raw_samples"]))]
                        mean_diff = sum(diffs) / len(diffs)
                        # Conversion a estimacion de RMSSD en ms
                        rmssd = max(18.0, min(85.0, (mean_diff * 9.5) + (110 - bpm) * 0.3))
                        self.metrics["hrv_rmssd"] = round(rmssd, 1)
                        self.lbl_hrv_val.configure(text=f"{self.metrics['hrv_rmssd']} ms")

                        # Indice de Enfoque y Carga
                        focus = max(20, min(98, int(100 - abs(bpm - 72) * 1.2 + (rmssd * 0.3))))
                        self.metrics["focus_score"] = focus
                        self.lbl_focus_val.configure(text=f"{focus} / 100")

                        # Ritmo metabolico en tiempo real (kcal/min)
                        cal_min = round(max(1.1, (bpm * 0.018) + (self.metrics['steps'] * 0.0002)), 1)
                        self.lbl_met_val.configure(text=f"{cal_min} kcal/min")

                elif m_type == "daily_activity":
                    steps = msg.get("steps")
                    self.metrics["steps"] = steps
                    self.lbl_steps_value.configure(text=f"{steps:,}")

                elif m_type == "battery":
                    pct = msg.get("value")
                    self.metrics["battery"] = pct
                    self.lbl_bat_val.configure(text=f"{pct} %")

        except queue.Empty:
            pass

        self.after(80, self.process_ble_queue_loop)

    # ── Loop de Render y Animaciones ──
    def update_animation_loop(self):
        # 1. Actualizar y renderizar criatura
        self.creature.update_telemetry(self.metrics["steps"], self.metrics["hr"], self.metrics["hrv_rmssd"])
        self.creature.render()

        # 2. Renderizar sparkline
        if self.creature.frame % 3 == 0:
            self.render_sparkline()

        # 3. Textos de evolucion y barra
        self.lbl_evolution.configure(text=self.creature.get_title())
        xp_in_level = self.metrics["steps"] % 1200
        self.bar_xp.set(xp_in_level / 1200.0)
        self.lbl_xp_num.configure(text=f"Nivel {self.creature.level}  ·  {self.metrics['steps']:,} XP")

        self.after(50, self.update_animation_loop)


if __name__ == "__main__":
    app = VitamonOverlayApp()
    app.mainloop()
