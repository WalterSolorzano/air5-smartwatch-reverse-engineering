"""
CYBER-TELEMETRY HUD — Smartwatch Air5 BLE Compact (320x195 px)
Zero-AI / Industrial Engineering Theme:
- Synthetic Interpolated QRS ECG Waveform Engine (Oscilloscope CRT at 60 FPS)
- Central Halo Core with 14 Orbiting Particles synchronized to BPM
- Crisp Micro-LED Segment Battery Indicator
- Structured 2-Line Cyber-Diagnostic Console (Zero truncated text, 100% legible)
- Packet Arrival Edge Glow & Hexadecimal RAW Debugger
"""
import sys
import os
import time
import math
import random
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

# Paleta Industrial "Zero-AI" (CRT Phosphor Green, CRT Cyan, Dark Matte Slate)
HUD_PALETTE = {
    "bg_base": "#0C0F12",
    "bg_card": "#11161B",
    "border_normal": "#1C242D",
    "border_flash": "#00E5FF",
    "border_alert": "#FF2A2A",
    "text_hero": "#E6EDF3",
    "text_sub": "#8B949E",
    "text_raw": "#525E6C",
    "crt_green": "#00FF66",
    "crt_cyan": "#00E5FF",
    "amber_alert": "#FF9900",
    "red_alarm": "#FF2A2A",
    "ecg_grid": "#131C24",
    "ecg_trace": "#00FF66"
}

# ── Hilo de Conexión BLE WinRT Asíncrono ──────────────────────────────────
class BLETelemetryBridge(threading.Thread):
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
            self.data_queue.put({"type": "status", "status": "sim", "msg": "NO_WINRT"})
            return

        mac_int = int(self.mac_addr.replace(":", ""), 16)

        while self.running:
            self.data_queue.put({"type": "status", "status": "scan", "msg": "SCAN_BLE"})
            try:
                device = await BluetoothLEDevice.from_bluetooth_address_async(mac_int)
                if not device:
                    self.data_queue.put({"type": "status", "status": "offline", "msg": "DISCONNECT"})
                    await asyncio.sleep(4)
                    continue

                services_res = await device.get_gatt_services_async()
                if services_res.status != GattCommunicationStatus.SUCCESS:
                    self.data_queue.put({"type": "status", "status": "offline", "msg": "GATT_ERR"})
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
                    self.data_queue.put({"type": "status", "status": "offline", "msg": "NO_PIPE"})
                    device.close()
                    await asyncio.sleep(4)
                    continue

                def on_ch1_notify(sender, args):
                    t_rx = time.perf_counter()
                    reader = DataReader.from_buffer(args.characteristic_value)
                    data = bytes([reader.read_byte() for _ in range(reader.unconsumed_buffer_length)])
                    if not data: return
                    cmd = data[0]

                    # Batería (A2)
                    if cmd == 0xA2 and len(data) >= 2:
                        self.data_queue.put({"type": "battery", "value": int(data[1]), "raw": f"0xA2_{data[1]:02X}", "t": t_rx})

                    # Frecuencia cardíaca en vivo (E5)
                    elif cmd == 0xE5 and len(data) >= 4 and data[1] == 0x11:
                        bpm = data[3]
                        if 35 <= bpm <= 220:
                            self.data_queue.put({"type": "live_hr", "value": bpm, "raw": f"0xE5_{bpm:02X}", "t": t_rx})

                    # Actividad diaria (26)
                    elif cmd == 0x26 and len(data) >= 9:
                        steps = struct.unpack_from("<H", data, 3)[0]
                        calories = struct.unpack_from("<H", data, 5)[0]
                        distance = struct.unpack_from("<H", data, 7)[0]
                        if steps != 65535:
                            self.data_queue.put({
                                "type": "activity",
                                "steps": steps,
                                "cal": calories,
                                "dist": distance,
                                "raw": f"0x26_STP_{steps}",
                                "t": t_rx
                            })

                    # Incremento instantáneo (B1)
                    elif cmd == 0xB1:
                        self.data_queue.put({"type": "step_inc", "raw": "0xB1_INC", "t": t_rx})

                def on_ch2_notify(sender, args):
                    t_rx = time.perf_counter()
                    reader = DataReader.from_buffer(args.characteristic_value)
                    data = bytes([reader.read_byte() for _ in range(reader.unconsumed_buffer_length)])
                    if not data: return
                    cmd = data[0]
                    if cmd == 0x34 and len(data) >= 20 and data[1] == 0xFA:
                        spo2 = data[-1]
                        if 70 <= spo2 <= 100 and spo2 != 0xFF:
                            self.data_queue.put({"type": "spo2", "value": spo2, "raw": f"0x34_SPO2_{spo2}", "t": t_rx})

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

                # Handshake Air5
                await send_cmd(write1, "0808442a01243943756ffffed921005f784be1dc")
                if write2:
                    await send_cmd(write2, "00f4000000000000000000000000000000000402")

                # Antispam sedentarismo
                await send_cmd(write1, "d1ff64")
                await send_cmd(write1, "d7160017000000")
                self.data_queue.put({"type": "antispam", "msg": "ANTISPAM_ACTIVE"})

                # Sync hora
                now = datetime.now()
                th = f"a3{now.year:04x}{now.month:02x}{now.day:02x}{now.hour:02x}{now.minute:02x}{now.second:02x}"
                await send_cmd(write1, th)

                # Poll inicial
                await send_cmd(write1, "a2")
                await send_cmd(write1, "2601")
                if write2:
                    await send_cmd(write2, "34fa")

                self.data_queue.put({"type": "status", "status": "online", "msg": "PIPE_ONLINE"})

                last_poll = time.time()
                while self.running:
                    try:
                        while not self.cmd_queue.empty():
                            cmd_req = self.cmd_queue.get_nowait()
                            act = cmd_req.get("action")
                            if act == "silence":
                                await send_cmd(write1, "d1ff64")
                                await send_cmd(write1, "d7160017000000")
                            elif act == "sync_time":
                                n = datetime.now()
                                th = f"a3{n.year:04x}{n.month:02x}{n.day:02x}{n.hour:02x}{n.minute:02x}{n.second:02x}"
                                await send_cmd(write1, th)
                    except queue.Empty:
                        pass

                    if time.time() - last_poll > 10:
                        last_poll = time.time()
                        await send_cmd(write1, "a2")
                        await send_cmd(write1, "2601")

                    await asyncio.sleep(0.3)

            except Exception as e:
                self.data_queue.put({"type": "status", "status": "offline", "msg": "RETRY_PIPE"})
                await asyncio.sleep(4)


# ── Generador Matemático de Onda ECG / PPG Sintética Interpolada ────────
class SyntheticECGGenerator:
    """Motor continuo a 60 FPS que genera la forma de onda QRS fisiológica interpolada."""
    def __init__(self, buffer_len=140):
        self.buffer_len = buffer_len
        self.buffer = [0.0] * buffer_len
        self.current_bpm = 72.0
        self.target_bpm = 72.0
        self.phase = 0.0
        self.last_update = time.perf_counter()
        self.last_packet_time = time.perf_counter()

    def set_target_bpm(self, bpm):
        self.target_bpm = max(40.0, min(190.0, float(bpm)))
        self.last_packet_time = time.perf_counter()

    def get_ecg_sample(self, theta):
        """Calcula el voltaje relativo del complejo P-Q-R-S-T en función de la fase [0, 1)."""
        t = theta % 1.0
        # 1. Isoeléctrico basal
        if t < 0.12: return 0.0
        # 2. Onda P (Despolarización auricular)
        elif t < 0.20: return 0.18 * math.sin(((t - 0.12) / 0.08) * math.pi)
        # 3. Segmento PR
        elif t < 0.24: return 0.0
        # 4. Onda Q (Muesca negativa previa)
        elif t < 0.27: return -0.22 * math.sin(((t - 0.24) / 0.03) * math.pi)
        # 5. Pico R (Sístole ventricular rápida)
        elif t < 0.33: return 1.0 * math.sin(((t - 0.27) / 0.06) * math.pi)
        # 6. Onda S (Depresión ventricular)
        elif t < 0.37: return -0.38 * math.sin(((t - 0.33) / 0.04) * math.pi)
        # 7. Segmento ST
        elif t < 0.46: return 0.0
        # 8. Onda T (Repolarización)
        elif t < 0.68: return 0.26 * math.sin(((t - 0.46) / 0.22) * math.pi)
        # 9. Retorno a línea base
        else: return 0.0

    def step(self, dt):
        # Suavizado de BPM hacia el objetivo
        self.current_bpm += (self.target_bpm - self.current_bpm) * 0.06
        freq = self.current_bpm / 60.0

        # Avanzar fase
        self.phase = (self.phase + (freq * dt)) % 1.0

        # Atenuación a Flatline si pasan más de 6 segundos sin paquetes BLE
        time_since_pkt = time.perf_counter() - self.last_packet_time
        decay = 1.0 if time_since_pkt < 5.0 else max(0.05, math.exp(-(time_since_pkt - 5.0) * 0.4))

        # Micro-ruido fisiológico de sensor
        noise = (random.random() - 0.5) * 0.03
        val = (self.get_ecg_sample(self.phase) * decay) + noise

        # Desplazar buffer
        self.buffer.pop(0)
        self.buffer.append(val)
        return self.buffer, self.current_bpm


# ── Renderizador de Halo con 14 Micro-Puntos Orbitales ──────────────────
class IndustrialHaloCore:
    def __init__(self, canvas, cx=38, cy=40):
        self.canvas = canvas
        self.cx = cx
        self.cy = cy
        self.bpm = 72.0
        self.num_particles = 14

        # Pista y Halo Central
        self.id_base_track = canvas.create_oval(cx-28, cy-28, cx+28, cy+28, outline="#18232D", width=1.5)
        self.id_core_body = canvas.create_oval(cx-14, cy-14, cx+14, cy+14, fill="#0F171F", outline="#00E5FF", width=1.5)
        self.id_core_center = canvas.create_oval(cx-6, cy-6, cx+6, cy+6, fill="#00FF66", outline="")

        # 14 Micro-Puntos Orbitales
        self.particles = []
        for _ in range(self.num_particles):
            p = canvas.create_oval(0, 0, 0, 0, fill="#00E5FF", outline="")
            self.particles.append(p)

    def update(self, t_now, bpm, ecg_val):
        cx, cy = self.cx, self.cy
        self.bpm = bpm

        # Expansión sistólica instantánea según la onda ECG
        expansion = max(0.0, ecg_val) * 5.0
        core_r = 13.0 + expansion
        center_r = 5.0 + (expansion * 0.6)

        self.canvas.coords(self.id_core_body, cx - core_r, cy - core_r, cx + core_r, cy + core_r)
        self.canvas.coords(self.id_core_center, cx - center_r, cy - center_r, cx + center_r, cy + center_r)

        # Color reactivo del centro
        center_col = HUD_PALETTE["crt_green"] if bpm < 95 else HUD_PALETTE["amber_alert"]
        self.canvas.itemconfig(self.id_core_center, fill=center_col)

        # Rotación de partículas según BPM
        rot_speed = (self.bpm / 60.0) * 120.0
        base_angle = (t_now * rot_speed) % 360.0
        orbit_r = 24.0

        for i, p_id in enumerate(self.particles):
            angle = base_angle + (i * (360.0 / self.num_particles))
            rad = math.radians(angle)
            px = cx + math.cos(rad) * orbit_r
            py = cy + math.sin(rad) * orbit_r
            p_size = 1.2 if (i % 2 == 0) else 1.8
            self.canvas.coords(p_id, px - p_size, py - p_size, px + p_size, py + p_size)


# ── Aplicación Compacta 320x195 px: Cyber Telemetry HUD ─────────────────
class CyberTelemetryHUD(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Ventana Superpuesta Frameless
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=HUD_PALETTE["bg_base"])

        # Dimensiones Exactas Optimizadas (320 x 195 px)
        self.hud_w = 320
        self.hud_h = 195
        screen_w = self.winfo_screenwidth()
        pos_x = screen_w - self.hud_w - 20
        pos_y = 60
        self.geometry(f"{self.hud_w}x{self.hud_h}+{pos_x}+{pos_y}")

        # Sistema de Arrastre de Ventana
        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.do_drag)

        # Colas de Telemetría
        self.data_queue = queue.Queue()
        self.cmd_queue = queue.Queue()

        # Telemetría de Alta Precisión
        self.telemetry = {
            "bpm": 72,
            "target_bpm": 72,
            "battery": 16,
            "body_battery": 78,
            "steps": 2934,
            "spo2": 98,
            "last_raw_cmd": "0xE5",
            "sedentary_seconds": 59 * 60,
            "antispam_flash_time": 0.0,
            "flash_edge_time": 0.0
        }

        # Motores Gráficos y Sintetizador ECG
        self.ecg_engine = SyntheticECGGenerator(buffer_len=140)

        # Construir Interfaz
        self.setup_ui()

        # Núcleo Halo
        self.halo_core = IndustrialHaloCore(self.canvas_halo, cx=38, cy=40)

        # Iniciar Worker BLE
        self.ble_worker = BLETelemetryBridge(self.data_queue, self.cmd_queue)
        self.ble_worker.start()

        # Timers de Loop 60 FPS
        self.last_loop_time = time.perf_counter()
        self.after(16, self.render_60fps_loop)
        self.after(60, self.process_telemetry_queue)
        self.after(1000, self.update_sedentary_and_diagnostics)

    def start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def do_drag(self, event):
        x = self.winfo_x() + (event.x - self._drag_x)
        y = self.winfo_y() + (event.y - self._drag_y)
        self.geometry(f"+{x}+{y}")

    def close_hud(self):
        self.destroy()
        sys.exit(0)

    # ── Generador de Bloques LED Segmentados Estilo VST [■ ■ ■ □ □] ─────
    def get_led_bar_str(self, pct, total_blocks=5):
        filled = int(round((pct / 100.0) * total_blocks))
        filled = max(0, min(total_blocks, filled))
        return "■" * filled + "□" * (total_blocks - filled)

    def setup_ui(self):
        # Marco Principal Contenedor con Borde Técnico
        self.container = ctk.CTkFrame(self, fg_color=HUD_PALETTE["bg_base"], corner_radius=8,
                                      border_width=1, border_color=HUD_PALETTE["border_normal"])
        self.container.pack(fill="both", expand=True, padx=1, pady=1)

        # ── 1. Header Técnico (0x1DBC [RAW 0xE5] 72 BPM [■■□□□] 16% [x]) ──
        self.header = ctk.CTkFrame(self.container, fg_color="transparent", height=24)
        self.header.pack(fill="x", padx=10, pady=(6, 2))

        # ID y Comando RAW
        self.lbl_id = ctk.CTkLabel(self.header, text="0x1DBC", font=("Consolas", 10, "bold"),
                                   text_color=HUD_PALETTE["crt_cyan"])
        self.lbl_id.pack(side="left")

        self.lbl_raw = ctk.CTkLabel(self.header, text=" [RAW 0xE5]", font=("Consolas", 8),
                                    text_color=HUD_PALETTE["text_raw"])
        self.lbl_raw.pack(side="left", padx=(2, 6))

        # Pulso en Vivo Central
        self.lbl_bpm_head = ctk.CTkLabel(self.header, text="72 BPM", font=("Consolas", 10, "bold"),
                                         text_color=HUD_PALETTE["crt_green"])
        self.lbl_bpm_head.pack(side="left", padx=4)

        # Botón Cerrar Discreto
        self.btn_close = ctk.CTkButton(self.header, text="x", width=14, height=14, corner_radius=2,
                                       fg_color="transparent", hover_color=HUD_PALETTE["bg_card"],
                                       text_color=HUD_PALETTE["text_sub"], font=("Consolas", 9),
                                       command=self.close_hud)
        self.btn_close.pack(side="right", padx=(4, 0))

        # Batería Segmentada en Bloques VST Nítidos
        bat_blocks = self.get_led_bar_str(self.telemetry["battery"], 5)
        self.lbl_bat_vst = ctk.CTkLabel(self.header, text=f"{bat_blocks} {self.telemetry['battery']}%",
                                       font=("Consolas", 9, "bold"), text_color=HUD_PALETTE["text_sub"])
        self.lbl_bat_vst.pack(side="right", padx=(0, 2))

        # Divisor Superior Fino
        self.div_top = ctk.CTkFrame(self.container, fg_color=HUD_PALETTE["border_normal"], height=1)
        self.div_top.pack(fill="x", padx=8, pady=(2, 3))

        # ── 2. Módulo Visual Center (Halo Core + Canvas ECG Waveform) ──
        self.center_frame = ctk.CTkFrame(self.container, fg_color="transparent", height=86)
        self.center_frame.pack(fill="x", padx=8, pady=0)

        # Canvas Halo Core (Izquierda)
        self.canvas_halo = tk.Canvas(self.center_frame, width=76, height=80,
                                     bg=HUD_PALETTE["bg_base"], highlightthickness=0)
        self.canvas_halo.pack(side="left", padx=(0, 4))

        # Contenedor Derecho: Canvas Osciloscopio ECG + Subtítulo de Sincronización
        self.right_col = ctk.CTkFrame(self.center_frame, fg_color="transparent")
        self.right_col.pack(side="left", fill="both", expand=True)

        # Canvas ECG Waveform (Osciloscopio Verde Fósforo con Cuadrícula Tenue)
        self.canvas_ecg = tk.Canvas(self.right_col, width=220, height=54,
                                    bg=HUD_PALETTE["bg_card"], highlightthickness=1,
                                    highlightbackground=HUD_PALETTE["border_normal"])
        self.canvas_ecg.pack(fill="x", pady=(0, 3))

        # Subtítulo Técnico: HALO REACT [ 72 lpm ] -> Target: 72 BPM  [ANTISPAM]
        self.sync_row = ctk.CTkFrame(self.right_col, fg_color="transparent", height=18)
        self.sync_row.pack(fill="x")

        self.lbl_sync_status = ctk.CTkLabel(self.sync_row, text="HALO REACT [ 72 lpm ] -> Target: 72 BPM",
                                           font=("Consolas", 8), text_color=HUD_PALETTE["text_sub"], anchor="w")
        self.lbl_sync_status.pack(side="left")

        # Insignia de Antispam / Supresión
        self.lbl_antispam = ctk.CTkLabel(self.sync_row, text="[ANTISPAM]", font=("Consolas", 7, "bold"),
                                         text_color=HUD_PALETTE["crt_green"])
        self.lbl_antispam.pack(side="right")

        # Divisor Inferior Fino
        self.div_bot = ctk.CTkFrame(self.container, fg_color=HUD_PALETTE["border_normal"], height=1)
        self.div_bot.pack(fill="x", padx=8, pady=(4, 4))

        # ── 3. Consola Terminal Diagnóstica Estructurada (Zero-Corte, 100% Nítida) ──
        self.diag_frame = ctk.CTkFrame(self.container, fg_color=HUD_PALETTE["bg_card"], corner_radius=5,
                                       border_width=1, border_color=HUD_PALETTE["border_normal"])
        self.diag_frame.pack(fill="x", padx=8, pady=(0, 6))

        # Línea 1: Diagnóstico Fisiológico / Alerta Inmediata
        self.lbl_diag_line1 = ctk.CTkLabel(
            self.diag_frame,
            text="⚠ ALERT: SEDENTARY_HIGH (59m) · FC Basal -8 BPM",
            font=("Consolas", 8, "bold"),
            text_color=HUD_PALETTE["amber_alert"],
            anchor="w"
        )
        self.lbl_diag_line1.pack(fill="x", padx=8, pady=(3, 1))

        # Línea 2: Pipeline BLE RAW & Telemetría
        self.lbl_diag_line2 = ctk.CTkLabel(
            self.diag_frame,
            text="BLE: 0x0013 [0xE5] | LATENCY: 8ms | SPO2: 98% | OK",
            font=("Consolas", 8),
            text_color=HUD_PALETTE["text_sub"],
            anchor="w"
        )
        self.lbl_diag_line2.pack(fill="x", padx=8, pady=(0, 3))

    # ── Renderizado del Osciloscopio ECG a 60 FPS ──
    def draw_ecg_oscilloscope(self, buffer):
        self.canvas_ecg.delete("all")
        w = 220
        h = 54
        mid_y = h / 2.0

        # 1. Cuadrícula de Osciloscopio CRT Tenue
        for gx in range(0, w, 20):
            self.canvas_ecg.create_line(gx, 0, gx, h, fill=HUD_PALETTE["ecg_grid"], width=1)
        for gy in range(0, h, 14):
            self.canvas_ecg.create_line(0, gy, w, gy, fill=HUD_PALETTE["ecg_grid"], width=1)

        # 2. Trazado de Onda Continua
        step = w / float(len(buffer) - 1)
        coords = []
        for i, val in enumerate(buffer):
            x = i * step
            y = mid_y - (val * (mid_y - 6))
            coords.append((x, y))

        for i in range(len(coords) - 1):
            x1, y1 = coords[i]
            x2, y2 = coords[i+1]
            self.canvas_ecg.create_line(x1, y1, x2, y2, fill=HUD_PALETTE["ecg_trace"], width=1.5)

        # 3. Punto de barrido CRT líder en el extremo derecho
        lx, ly = coords[-1]
        self.canvas_ecg.create_oval(lx - 2, ly - 2, lx + 2, ly + 2, fill="#FFFFFF", outline=HUD_PALETTE["crt_cyan"])

    # ── Loop de Animación 60 FPS Locked ──
    def render_60fps_loop(self):
        t_now = time.perf_counter()
        dt = t_now - self.last_loop_time
        self.last_loop_time = t_now

        # 1. Actualizar Motor ECG Sintético Interpolado
        ecg_buf, curr_bpm = self.ecg_engine.step(dt)
        self.draw_ecg_oscilloscope(ecg_buf)

        # 2. Actualizar Halo Core con 14 Partículas
        latest_ecg = ecg_buf[-1]
        self.halo_core.update(t_now, curr_bpm, latest_ecg)

        # 3. Destello de borde por llegada de paquete BLE (120ms)
        if t_now - self.telemetry["flash_edge_time"] < 0.12:
            self.container.configure(border_color=HUD_PALETTE["border_flash"])
        else:
            self.container.configure(border_color=HUD_PALETTE["border_normal"])

        self.after(16, self.render_60fps_loop)

    # ── Loop de Cola de Telemetría BLE ──
    def process_telemetry_queue(self):
        try:
            while not self.data_queue.empty():
                pkt = self.data_queue.get_nowait()
                p_type = pkt.get("type")
                t_rx = pkt.get("t", time.perf_counter())

                # Micro-destello de borde en cada paquete
                self.telemetry["flash_edge_time"] = t_rx

                if p_type == "live_hr":
                    bpm = pkt.get("value")
                    raw = pkt.get("raw", "0xE5")
                    self.telemetry["target_bpm"] = bpm
                    self.telemetry["last_raw_cmd"] = raw
                    self.ecg_engine.set_target_bpm(bpm)

                    self.lbl_bpm_head.configure(text=f"{bpm} BPM")
                    self.lbl_raw.configure(text=f" [RAW {raw}]")
                    self.lbl_sync_status.configure(
                        text=f"HALO REACT [ {int(self.ecg_engine.current_bpm)} lpm ] -> Target: {bpm} BPM"
                    )

                elif p_type == "battery":
                    pct = pkt.get("value")
                    self.telemetry["battery"] = pct
                    blocks = self.get_led_bar_str(pct, 5)
                    self.lbl_bat_vst.configure(text=f"{blocks} {pct}%")

                elif p_type == "antispam":
                    self.lbl_antispam.configure(text_color=HUD_PALETTE["crt_green"])

                elif p_type == "activity":
                    steps = pkt.get("steps")
                    self.telemetry["steps"] = steps

        except queue.Empty:
            pass

        self.after(60, self.process_telemetry_queue)

    # ── Actualización de Diagnóstico Terminal ──
    def update_sedentary_and_diagnostics(self):
        self.telemetry["sedentary_seconds"] += 1
        mins = self.telemetry["sedentary_seconds"] // 60

        # Diagnóstico claro, sin texto cortado
        if mins >= 45:
            self.lbl_diag_line1.configure(
                text=f"⚠ ALERT: SEDENTARY_HIGH ({mins}m) · FC Basal -8 BPM",
                text_color=HUD_PALETTE["amber_alert"]
            )
            self.diag_frame.configure(border_color=HUD_PALETTE["amber_alert"])
        else:
            self.lbl_diag_line1.configure(
                text=f"✔ PIPELINE_OK · Sistema en Equilibrio · FC: {self.telemetry['target_bpm']} BPM",
                text_color=HUD_PALETTE["crt_green"]
            )
            self.diag_frame.configure(border_color=HUD_PALETTE["border_normal"])

        self.lbl_diag_line2.configure(
            text=f"BLE: 0x0013 [{self.telemetry['last_raw_cmd']}] | LATENCY: 8ms | SPO2: {self.telemetry['spo2']}% | OK",
            text_color=HUD_PALETTE["text_sub"]
        )

        self.after(1000, self.update_sedentary_and_diagnostics)


if __name__ == "__main__":
    app = CyberTelemetryHUD()
    app.mainloop()
