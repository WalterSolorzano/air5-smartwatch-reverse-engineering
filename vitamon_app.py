"""
JARVIS BIOMETRIC HUD — Smartwatch Air5 BLE Ultra-Smooth 60 FPS Edition
Optimizaciones de alto rendimiento: renderizado por coordenadas pre-asignadas (zero-garbage-collection),
animación procedural por delta-time exacto y modo cápsula por doble clic.
"""
import sys
import os
import time
import math
import threading
import queue
import struct
from datetime import datetime
import tkinter as tk
import customtkinter as ctk

# ── Configuracion Base de CustomTkinter ──────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

MAC_ADDR = "81:0A:B7:00:1D:BC"

# Paleta exacta de la referencia (Deep Navy / Cyber Red / Phosphor Cyan)
HUD_THEME = {
    "bg_window": "#141C27",
    "bg_card": "#182232",
    "border_window": "#27374D",
    "border_subtle": "#223044",
    "text_hero": "#F1F5F9",
    "text_muted": "#8EA0B8",
    "text_mono": "#CBD5E1",
    "red_alert": "#FF4A5A",
    "red_glow": "#E11D48",
    "red_banner_bg": "#2B141B",
    "red_banner_border": "#7F1D2E",
    "cyan_ok": "#00E5A3",
    "blue_banner_bg": "#162334",
    "blue_banner_border": "#233D5B",
    "bar_track": "#27364B",
    "bar_fill": "#E2E8F0"
}

# ── Hilo de Conexion BLE WinRT Robusto ───────────────────────────────────
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
            self.data_queue.put({"type": "status", "status": "connecting", "msg": "CONECTANDO"})
            try:
                device = await BluetoothLEDevice.from_bluetooth_address_async(mac_int)
                if not device:
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "DESCONECTADO"})
                    await asyncio.sleep(4)
                    continue

                services_res = await device.get_gatt_services_async()
                if services_res.status != GattCommunicationStatus.SUCCESS:
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "FALLO_GATT"})
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
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "SIN_CANALES"})
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
                            self.data_queue.put({"type": "live_hr", "value": bpm})

                    # Actividad diaria (26)
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

                    # Incremento de pasos en vivo (B1)
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

                # Handshake
                await send_cmd(write1, "0808442a01243943756ffffed921005f784be1dc")
                if write2:
                    await send_cmd(write2, "00f4000000000000000000000000000000000402")

                # Silenciar spam de sedentarismo
                await send_cmd(write1, "d1ff64")
                await send_cmd(write1, "d7160017000000")

                # Sincronizar hora
                now = datetime.now()
                th = f"a3{now.year:04x}{now.month:02x}{now.day:02x}{now.hour:02x}{now.minute:02x}{now.second:02x}"
                await send_cmd(write1, th)

                # Solicitar bateria y pasos iniciales
                await send_cmd(write1, "a2")
                await send_cmd(write1, "2601")
                if write2:
                    await send_cmd(write2, "34fa")

                self.data_queue.put({"type": "status", "status": "connected", "msg": "SISTEMA_OK"})

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
                self.data_queue.put({"type": "status", "status": "disconnected", "msg": "DESCONECTADO"})
                await asyncio.sleep(4)


# ── Motor del Núcleo HAL 9000 Optimizado a 60 FPS (Zero-Garbage) ─────────
class HighPerformanceReactor:
    """Motor gráfico de latido cardíaco optimizado a 60 FPS con items pre-construidos en Canvas."""
    def __init__(self, canvas, cx=44, cy=44):
        self.canvas = canvas
        self.cx = cx
        self.cy = cy
        self.bpm = 72
        self.smoothed_bpm = 72.0

        # Pre-crear todos los elementos en el canvas una sola vez (Zero Garbage Collection)
        # Anillos de carcasa
        self.id_ring3 = canvas.create_oval(cx-38, cy-38, cx+38, cy+38, fill="#0F1622", outline="#1F2D40", width=1.5)
        self.id_ring2 = canvas.create_oval(cx-32, cy-32, cx+32, cy+32, fill="#0A0F18", outline="#162232", width=1)
        self.id_ring1 = canvas.create_oval(cx-25, cy-25, cx+25, cy+25, fill="#06090E", outline="#111A26", width=1)

        # Resplandores difusos
        self.id_glow_outer = canvas.create_oval(cx-20, cy-20, cx+20, cy+20, fill="#2A0B12", outline="")
        self.id_glow_mid = canvas.create_oval(cx-16, cy-16, cx+16, cy+16, fill="#4C0D1A", outline="")

        # Núcleo e iris
        self.id_core = canvas.create_oval(cx-12, cy-12, cx+12, cy+12, fill="#E11D48", outline="#FF4A5A", width=1.5)
        self.id_center = canvas.create_oval(cx-6, cy-6, cx+6, cy+6, fill="#FF6B7A", outline="#FFA4AD", width=1)

        # Hendiduras / Slits de HAL 9000
        self.id_slits = [canvas.create_line(0, 0, 0, 0, fill="#0B0F16", width=1.5) for _ in range(5)]

        # Rayo láser de escaneo
        self.id_laser = canvas.create_line(0, 0, 0, 0, fill="#FFFFFF", width=1.5)

        # Partícula orbital
        self.id_orbit = canvas.create_oval(0, 0, 0, 0, fill="#FF4A5A", outline="")

    def set_bpm(self, bpm):
        self.bpm = max(40, min(190, bpm))

    def update_frame(self, t_now):
        cx, cy = self.cx, self.cy

        # Suavizado de transicion de BPM
        self.smoothed_bpm += (self.bpm - self.smoothed_bpm) * 0.08
        freq = self.smoothed_bpm / 60.0

        # Curva de latido cardíaco fisiológico (Asimétrica y continua)
        phase = (t_now * freq * 2.0 * math.pi) % (2.0 * math.pi)
        # Función de latido sístole-diástole suave
        pulse = math.sin(phase)
        pulse = max(0.0, pulse) ** 1.8

        # Dimensiones dinámicas
        glow_r = 15.0 + (pulse * 6.5)
        core_r = 11.5 + (pulse * 3.5)
        center_r = 5.0 + (pulse * 2.2)

        # 1. Actualizar resplandores (Solo coords)
        self.canvas.coords(self.id_glow_outer, cx - glow_r - 5, cy - glow_r - 5, cx + glow_r + 5, cy + glow_r + 5)
        self.canvas.coords(self.id_glow_mid, cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r)

        # 2. Actualizar núcleo central
        self.canvas.coords(self.id_core, cx - core_r, cy - core_r, cx + core_r, cy + core_r)
        self.canvas.coords(self.id_center, cx - center_r, cy - center_r, cx + center_r, cy + center_r)

        # 3. Actualizar hendiduras
        slit_offsets = [-7.0, -3.5, 0.0, 3.5, 7.0]
        for i, y_off in enumerate(slit_offsets):
            if abs(y_off) < core_r:
                slit_w = math.sqrt(max(0.0, core_r**2 - y_off**2))
                self.canvas.coords(self.id_slits[i], cx - slit_w, cy + y_off, cx + slit_w, cy + y_off)
            else:
                self.canvas.coords(self.id_slits[i], 0, 0, 0, 0)

        # 4. Rayo láser oscilante continuo
        scan_phase = (t_now * 3.2) % (2.0 * math.pi)
        scan_y = cy + (math.sin(scan_phase) * (core_r - 2.5))
        scan_w = math.sqrt(max(0.0, core_r**2 - (scan_y - cy)**2))
        self.canvas.coords(self.id_laser, cx - scan_w, scan_y, cx + scan_w, scan_y)

        # 5. Partícula orbital fluida
        orb_angle = (t_now * 160.0) % 360.0
        rad = math.radians(orb_angle)
        ox = cx + math.cos(rad) * 28.0
        oy = cy + math.sin(rad) * 28.0
        self.canvas.coords(self.id_orbit, ox - 1.5, oy - 1.5, ox + 1.5, oy + 1.5)


# ── Aplicacion Principal: Jarvis HUD 60 FPS ─────────────────────────────
class JarvisHUDApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Ventana Superpuesta Frameless
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=HUD_THEME["bg_window"])

        # Dimensiones y Posicionamiento Inicial
        self.width_full = 330
        self.height_full = 250
        self.is_mini = False

        screen_w = self.winfo_screenwidth()
        pos_x = screen_w - self.width_full - 24
        pos_y = 60
        self.geometry(f"{self.width_full}x{self.height_full}+{pos_x}+{pos_y}")

        # Sistema de arrastre suave en cualquier parte
        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.do_drag)
        self.bind("<Double-Button-1>", self.toggle_mini_mode)

        # Colas de comunicación
        self.data_queue = queue.Queue()
        self.cmd_queue = queue.Queue()

        # Telemetría y estado
        self.metrics = {
            "hr": 72,
            "hr_samples": [72],
            "stress_pct": 0.28,
            "battery": 16,
            "steps": 2934,
            "spo2": 98,
            "system_status": "SISTEMA_OK",
            "sedentary_seconds": 50 * 60,
            "last_step_count": 2934
        }

        # Construir Interfaz
        self.setup_ui()

        # Inicializar Motor Reactor Ultra-Smooth
        self.reactor = HighPerformanceReactor(self.canvas_reactor, cx=44, cy=44)

        # Iniciar Worker BLE en background
        self.ble_thread = BLEBridgeThread(self.data_queue, self.cmd_queue)
        self.ble_thread.start()

        # Ciclo de 60 FPS locked (~16ms)
        self.last_anim_time = time.perf_counter()
        self.after(16, self.render_60fps_loop)

        # Ciclos de datos desacoplados (para no sobrecargar el hilo UI)
        self.after(100, self.process_ble_queue_loop)
        self.after(1000, self.update_sedentary_timer_loop)

    def start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def do_drag(self, event):
        x = self.winfo_x() + (event.x - self._drag_x)
        y = self.winfo_y() + (event.y - self._drag_y)
        self.geometry(f"+{x}+{y}")

    def toggle_mini_mode(self, event=None):
        self.is_mini = not self.is_mini
        if self.is_mini:
            self.geometry(f"190x100")
            self.header.pack_forget()
            self.banner_alert.pack_forget()
            self.sep_line.pack_forget()
            self.row_stress.pack_forget()
            self.row_bat.pack_forget()
            self.footer.pack_forget()
        else:
            self.geometry(f"{self.width_full}x{self.height_full}")
            self.container.pack_forget()
            self.container.pack(fill="both", expand=True, padx=1, pady=1)
            self.header.pack(fill="x", padx=14, pady=(10, 4))
            self.banner_alert.pack(fill="x", padx=14, pady=(2, 8))
            self.body_frame.pack(fill="both", expand=True, padx=14, pady=(0, 6))
            self.sep_line.pack(fill="x", pady=(0, 6))
            self.row_stress.pack(fill="x", pady=2)
            self.row_bat.pack(fill="x", pady=2)
            self.footer.pack(fill="x", padx=14, pady=(0, 10))

    def close_hud(self):
        self.destroy()
        sys.exit(0)

    def setup_ui(self):
        # Marco Contenedor Principal
        self.container = ctk.CTkFrame(self, fg_color=HUD_THEME["bg_window"], corner_radius=14,
                                      border_width=1, border_color=HUD_THEME["border_window"])
        self.container.pack(fill="both", expand=True, padx=1, pady=1)

        # ── 1. Barra de Titulo (• EN VIVO / JARVIS ... SISTEMA_OK) ──
        self.header = ctk.CTkFrame(self.container, fg_color="transparent")
        self.header.pack(fill="x", padx=14, pady=(10, 4))

        self.frame_hdr_left = ctk.CTkFrame(self.header, fg_color="transparent")
        self.frame_hdr_left.pack(side="left")

        self.dot_live = ctk.CTkLabel(self.frame_hdr_left, text="•", font=("Segoe UI", 16, "bold"), text_color=HUD_THEME["red_alert"])
        self.dot_live.pack(side="left", padx=(0, 4))

        self.lbl_hdr_title = ctk.CTkLabel(self.frame_hdr_left, text="EN VIVO  /  JARVIS",
                                         font=("Consolas", 10, "bold"), text_color=HUD_THEME["text_hero"])
        self.lbl_hdr_title.pack(side="left")

        # Boton Cerrar Discreto
        self.btn_close = ctk.CTkButton(self.header, text="x", width=18, height=18, corner_radius=9,
                                       fg_color="transparent", hover_color=HUD_THEME["bg_card"],
                                       text_color=HUD_THEME["text_muted"], font=("Segoe UI", 9),
                                       command=self.close_hud)
        self.btn_close.pack(side="right", padx=(4, 0))

        # SISTEMA_OK
        self.lbl_status = ctk.CTkLabel(self.header, text="SISTEMA_OK", font=("Consolas", 10, "bold"),
                                       text_color=HUD_THEME["cyan_ok"])
        self.lbl_status.pack(side="right")

        # ── 2. Banner de Alerta / Sedentarismo ──
        self.banner_alert = ctk.CTkFrame(self.container, fg_color=HUD_THEME["red_banner_bg"], corner_radius=6,
                                         border_width=1, border_color=HUD_THEME["red_banner_border"], height=28)
        self.banner_alert.pack(fill="x", padx=14, pady=(2, 8))

        self.lbl_banner_text = ctk.CTkLabel(self.banner_alert, text="¡MUÉVETE!  50 min sentado",
                                           font=("Consolas", 9, "bold"), text_color=HUD_THEME["red_alert"])
        self.lbl_banner_text.pack(side="left", padx=10, pady=4)

        self.lbl_banner_icon = ctk.CTkLabel(self.banner_alert, text="/!\\", font=("Consolas", 9, "bold"),
                                            text_color=HUD_THEME["red_alert"])
        self.lbl_banner_icon.pack(side="right", padx=10, pady=4)

        # ── 3. Cuerpo Central (Reactor + Metricas) ──
        self.body_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.body_frame.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        # Reactor HAL 9000
        self.canvas_reactor = tk.Canvas(self.body_frame, width=88, height=88,
                                        bg=HUD_THEME["bg_window"], highlightthickness=0)
        self.canvas_reactor.pack(side="left", padx=(0, 10), pady=0)

        # Panel de Estadísticas
        self.stats_frame = ctk.CTkFrame(self.body_frame, fg_color="transparent")
        self.stats_frame.pack(side="left", fill="both", expand=True)

        # PULSO_VIVO 72 LPM
        self.row_hr = ctk.CTkFrame(self.stats_frame, fg_color="transparent")
        self.row_hr.pack(fill="x", pady=(0, 2))

        self.lbl_hr_tag = ctk.CTkLabel(self.row_hr, text="PULSO_VIVO", font=("Consolas", 10, "bold"),
                                       text_color=HUD_THEME["red_alert"])
        self.lbl_hr_tag.pack(side="left")

        self.lbl_hr_unit = ctk.CTkLabel(self.row_hr, text="LPM", font=("Consolas", 8),
                                        text_color=HUD_THEME["red_alert"])
        self.lbl_hr_unit.pack(side="right", padx=(2, 0))

        self.lbl_hr_num = ctk.CTkLabel(self.row_hr, text="72", font=("Consolas", 15, "bold"),
                                       text_color=HUD_THEME["red_alert"])
        self.lbl_hr_num.pack(side="right")

        # Divisor
        self.sep_line = ctk.CTkFrame(self.stats_frame, fg_color=HUD_THEME["border_subtle"], height=1)
        self.sep_line.pack(fill="x", pady=(0, 6))

        # ESTRÉS
        self.row_stress = ctk.CTkFrame(self.stats_frame, fg_color="transparent")
        self.row_stress.pack(fill="x", pady=2)

        self.lbl_stress_tag = ctk.CTkLabel(self.row_stress, text="ESTRÉS", width=55, anchor="w",
                                          font=("Consolas", 9), text_color=HUD_THEME["text_muted"])
        self.lbl_stress_tag.pack(side="left")

        self.bar_stress = ctk.CTkProgressBar(self.row_stress, height=6, width=95,
                                             progress_color=HUD_THEME["bar_fill"],
                                             fg_color=HUD_THEME["bar_track"])
        self.bar_stress.pack(side="left", padx=6)
        self.bar_stress.set(0.3)

        self.lbl_stress_val = ctk.CTkLabel(self.row_stress, text="BAJO", width=38, anchor="e",
                                          font=("Consolas", 9, "bold"), text_color=HUD_THEME["text_hero"])
        self.lbl_stress_val.pack(side="right")

        # BATERÍA
        self.row_bat = ctk.CTkFrame(self.stats_frame, fg_color="transparent")
        self.row_bat.pack(fill="x", pady=2)

        self.lbl_bat_tag = ctk.CTkLabel(self.row_bat, text="BATERÍA", width=55, anchor="w",
                                        font=("Consolas", 9), text_color=HUD_THEME["text_muted"])
        self.lbl_bat_tag.pack(side="left")

        self.bar_bat = ctk.CTkProgressBar(self.row_bat, height=6, width=95,
                                          progress_color=HUD_THEME["bar_fill"],
                                          fg_color=HUD_THEME["bar_track"])
        self.bar_bat.pack(side="left", padx=6)
        self.bar_bat.set(0.16)

        self.lbl_bat_val = ctk.CTkLabel(self.row_bat, text="16%", width=38, anchor="e",
                                        font=("Consolas", 9, "bold"), text_color=HUD_THEME["text_hero"])
        self.lbl_bat_val.pack(side="right")

        # ── 4. Footer: TIEMPO SENTADO ──
        self.footer = ctk.CTkFrame(self.container, fg_color="transparent")
        self.footer.pack(fill="x", padx=14, pady=(0, 10))

        self.lbl_time_tag = ctk.CTkLabel(self.footer, text="TIEMPO SENTADO", font=("Consolas", 9),
                                         text_color=HUD_THEME["text_muted"])
        self.lbl_time_tag.pack(side="left")

        self.lbl_time_val = ctk.CTkLabel(self.footer, text="50:00", font=("Consolas", 12, "bold"),
                                         text_color=HUD_THEME["red_alert"])
        self.lbl_time_val.pack(side="right")

    # ── Loop a 60 FPS (16.6ms) con Delta-Time Exacto ──
    def render_60fps_loop(self):
        t_now = time.perf_counter()
        self.reactor.set_bpm(self.metrics["hr"])
        self.reactor.update_frame(t_now)
        # Siguiente frame en exactamente 16ms
        self.after(16, self.render_60fps_loop)

    # ── Loop de Temporizador de Sedentarismo (1 Hz) ──
    def update_sedentary_timer_loop(self):
        self.metrics["sedentary_seconds"] += 1
        secs = self.metrics["sedentary_seconds"]
        mins = secs // 60
        rem_secs = secs % 60

        time_str = f"{mins:02d}:{rem_secs:02d}"
        self.lbl_time_val.configure(text=time_str)

        # Actualizar banner dinamicamente
        if mins >= 45:
            self.banner_alert.configure(fg_color=HUD_THEME["red_banner_bg"], border_color=HUD_THEME["red_banner_border"])
            self.lbl_banner_text.configure(text=f"¡MUÉVETE!  {mins} min sentado", text_color=HUD_THEME["red_alert"])
            self.lbl_banner_icon.configure(text="/!\\", text_color=HUD_THEME["red_alert"])
            self.lbl_time_val.configure(text_color=HUD_THEME["red_alert"])
        else:
            self.banner_alert.configure(fg_color=HUD_THEME["blue_banner_bg"], border_color=HUD_THEME["blue_banner_border"])
            self.lbl_banner_text.configure(text=f"MODO ENFOQUE  ·  {mins}m", text_color=HUD_THEME["cyan_ok"])
            self.lbl_banner_icon.configure(text="[OK]", text_color=HUD_THEME["cyan_ok"])
            self.lbl_time_val.configure(text_color=HUD_THEME["text_hero"])

        self.after(1000, self.update_sedentary_timer_loop)

    # ── Loop de Telemetria BLE Asincrona ──
    def process_ble_queue_loop(self):
        try:
            while not self.data_queue.empty():
                msg = self.data_queue.get_nowait()
                m_type = msg.get("type")

                if m_type == "status":
                    text = msg.get("msg")
                    self.lbl_status.configure(text=text)
                    if text == "SISTEMA_OK":
                        self.dot_live.configure(text_color=HUD_THEME["red_alert"])
                        self.lbl_status.configure(text_color=HUD_THEME["cyan_ok"])
                    else:
                        self.lbl_status.configure(text_color=HUD_THEME["text_muted"])

                elif m_type == "live_hr":
                    bpm = msg.get("value")
                    self.metrics["hr"] = bpm
                    self.lbl_hr_num.configure(text=str(bpm))

                    # Calcular nivel de estres segun variabilidad
                    self.metrics["hr_samples"].append(bpm)
                    if len(self.metrics["hr_samples"]) > 15:
                        self.metrics["hr_samples"].pop(0)

                    if len(self.metrics["hr_samples"]) >= 4:
                        diffs = [abs(self.metrics["hr_samples"][i] - self.metrics["hr_samples"][i-1])
                                 for i in range(1, len(self.metrics["hr_samples"]))]
                        mean_diff = sum(diffs) / len(diffs)
                        stress_score = max(0.1, min(0.95, 1.0 - (mean_diff / 8.0) + ((bpm - 70) * 0.006)))
                        self.bar_stress.set(stress_score)
                        if stress_score < 0.4:
                            self.lbl_stress_val.configure(text="BAJO", text_color=HUD_THEME["cyan_ok"])
                        elif stress_score < 0.7:
                            self.lbl_stress_val.configure(text="MEDIO", text_color=HUD_THEME["text_hero"])
                        else:
                            self.lbl_stress_val.configure(text="ALTO", text_color=HUD_THEME["red_alert"])

                elif m_type == "battery":
                    pct = msg.get("value")
                    self.metrics["battery"] = pct
                    self.lbl_bat_val.configure(text=f"{pct}%")
                    self.bar_bat.set(pct / 100.0)

                elif m_type == "daily_activity":
                    steps = msg.get("steps")
                    if steps > self.metrics["last_step_count"] + 20:
                        self.metrics["sedentary_seconds"] = 0
                        self.metrics["last_step_count"] = steps

                elif m_type == "step_inc":
                    self.metrics["sedentary_seconds"] = 0

        except queue.Empty:
            pass

        self.after(100, self.process_ble_queue_loop)


if __name__ == "__main__":
    app = JarvisHUDApp()
    app.mainloop()
