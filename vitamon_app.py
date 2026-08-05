"""
CYBER-TELEMETRY HUD — Master Unified Edition (390x180 px)
Integra simultáneamente y en vivo:
1. El NÚCLEO HALO / CYBER-HEART (14 partículas orbitales con sístole y pulso reactivo).
2. El STICKMAN RAGDOLL FÍSICO (Gravedad, salto por pasos, sueño sedentario y rebote).
3. El OSCILOSCOPIO ECG CRT a 60 FPS (Con parallax y flatline).
4. La FILA DE INSTRUMENTACIÓN 300ms (⏱ 59m, 🫁 98%, 👟 2.9k, ⚡ 16%, 🛡).
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

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

MAC_ADDR = "81:0A:B7:00:1D:BC"

PALETTE = {
    "bg_base": "#0A0D10",
    "bg_card": "#0F141A",
    "bg_pill": "#141B22",
    "border_normal": "#1B242D",
    "border_flash": "#00E5FF",
    "border_amber": "#FF9900",
    "border_red": "#FF3344",
    "crt_green": "#00FF66",
    "crt_cyan": "#00E5FF",
    "amber": "#FF9900",
    "red_alarm": "#FF3344",
    "text_hero": "#FFFFFF",
    "text_sub": "#8B949E",
    "ecg_grid": "#141C24"
}

# ── Hilo BLE WinRT Asíncrono ─────────────────────────────────────────────
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
            self.data_queue.put({"type": "status", "status": "sim"})
            return

        mac_int = int(self.mac_addr.replace(":", ""), 16)

        while self.running:
            self.data_queue.put({"type": "status", "status": "scan"})
            try:
                device = await BluetoothLEDevice.from_bluetooth_address_async(mac_int)
                if not device:
                    self.data_queue.put({"type": "status", "status": "offline"})
                    await asyncio.sleep(4)
                    continue

                services_res = await device.get_gatt_services_async()
                if services_res.status != GattCommunicationStatus.SUCCESS:
                    self.data_queue.put({"type": "status", "status": "offline"})
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
                    self.data_queue.put({"type": "status", "status": "offline"})
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
                        self.data_queue.put({"type": "battery", "value": int(data[1]), "t": t_rx})

                    # Frecuencia cardíaca (E5)
                    elif cmd == 0xE5 and len(data) >= 4 and data[1] == 0x11:
                        bpm = data[3]
                        if 35 <= bpm <= 220:
                            self.data_queue.put({"type": "live_hr", "value": bpm, "t": t_rx})

                    # Actividad diaria (26)
                    elif cmd == 0x26 and len(data) >= 9:
                        steps = struct.unpack_from("<H", data, 3)[0]
                        calories = struct.unpack_from("<H", data, 5)[0]
                        if steps != 65535:
                            self.data_queue.put({"type": "activity", "steps": steps, "cal": calories, "t": t_rx})

                    # Incremento instantáneo (B1)
                    elif cmd == 0xB1:
                        self.data_queue.put({"type": "step_inc", "t": t_rx})

                def on_ch2_notify(sender, args):
                    t_rx = time.perf_counter()
                    reader = DataReader.from_buffer(args.characteristic_value)
                    data = bytes([reader.read_byte() for _ in range(reader.unconsumed_buffer_length)])
                    if not data: return
                    cmd = data[0]
                    if cmd == 0x34 and len(data) >= 20 and data[1] == 0xFA:
                        spo2 = data[-1]
                        if 70 <= spo2 <= 100 and spo2 != 0xFF:
                            self.data_queue.put({"type": "spo2", "value": spo2, "t": t_rx})

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

                # Antispam
                await send_cmd(write1, "d1ff64")
                await send_cmd(write1, "d7160017000000")
                self.data_queue.put({"type": "antispam", "msg": "ACTIVE"})

                # Sync hora
                now = datetime.now()
                th = f"a3{now.year:04x}{now.month:02x}{now.day:02x}{now.hour:02x}{now.minute:02x}{now.second:02x}"
                await send_cmd(write1, th)

                # Poll inicial
                await send_cmd(write1, "a2")
                await send_cmd(write1, "2601")
                if write2:
                    await send_cmd(write2, "34fa")

                self.data_queue.put({"type": "status", "status": "online"})

                last_poll = time.time()
                while self.running:
                    try:
                        while not self.cmd_queue.empty():
                            cmd_req = self.cmd_queue.get_nowait()
                            act = cmd_req.get("action")
                            if act == "silence":
                                await send_cmd(write1, "d1ff64")
                                await send_cmd(write1, "d7160017000000")
                    except queue.Empty:
                        pass

                    if time.time() - last_poll > 10:
                        last_poll = time.time()
                        await send_cmd(write1, "a2")
                        await send_cmd(write1, "2601")

                    await asyncio.sleep(0.3)

            except Exception:
                self.data_queue.put({"type": "status", "status": "offline"})
                await asyncio.sleep(4)


# ── Motor ECG Fisiológico ───────────────────────────────────────────────
class SyntheticECGGenerator:
    def __init__(self, buffer_len=120):
        self.buffer_len = buffer_len
        self.buffer = [0.0] * buffer_len
        self.current_bpm = 72.0
        self.target_bpm = 72.0
        self.phase = 0.0
        self.last_packet_time = time.perf_counter()
        self.is_flatline = False
        self.parallax_offset = 0.0

    def set_target_bpm(self, bpm):
        self.target_bpm = max(40.0, min(190.0, float(bpm)))
        self.last_packet_time = time.perf_counter()

    def get_qrs_sample(self, t):
        t = t % 1.0
        if t < 0.12: return 0.0
        elif t < 0.20: return 0.18 * math.sin(((t - 0.12) / 0.08) * math.pi)
        elif t < 0.24: return 0.0
        elif t < 0.27: return -0.22 * math.sin(((t - 0.24) / 0.03) * math.pi)
        elif t < 0.33: return 1.0 * math.sin(((t - 0.27) / 0.06) * math.pi)
        elif t < 0.37: return -0.38 * math.sin(((t - 0.33) / 0.04) * math.pi)
        elif t < 0.46: return 0.0
        elif t < 0.68: return 0.26 * math.sin(((t - 0.46) / 0.22) * math.pi)
        else: return 0.0

    def step(self, dt, walking_speed=0.0):
        self.current_bpm += (self.target_bpm - self.current_bpm) * 0.06
        freq = self.current_bpm / 60.0
        self.phase = (self.phase + (freq * dt)) % 1.0

        self.parallax_offset = (self.parallax_offset + (walking_speed * dt * 40.0)) % 20.0

        time_since_pkt = time.perf_counter() - self.last_packet_time
        if time_since_pkt > 10.0:
            self.is_flatline = True
            val = (random.random() - 0.5) * 0.04
        else:
            self.is_flatline = False
            noise = (random.random() - 0.5) * 0.02
            val = self.get_qrs_sample(self.phase) + noise

        self.buffer.pop(0)
        self.buffer.append(val)
        return self.buffer, self.current_bpm, self.is_flatline


# ── MÓDULO 1: NÚCLEO HALO / CYBER-HEART REACTOR (14 Partículas Orbitales) ─
class IndustrialHaloCore:
    def __init__(self, canvas, cx=36, cy=36):
        self.canvas = canvas
        self.cx = cx
        self.cy = cy
        self.num_particles = 14

        self.id_base_track = canvas.create_oval(cx-26, cy-26, cx+26, cy+26, outline="#16202A", width=1.5)
        self.id_core_body = canvas.create_oval(cx-13, cy-13, cx+13, cy+13, fill="#0F171F", outline=PALETTE["crt_cyan"], width=1.5)
        self.id_core_center = canvas.create_oval(cx-5, cy-5, cx+5, cy+5, fill=PALETTE["crt_green"], outline="")

        self.particles = [canvas.create_oval(0, 0, 0, 0, fill=PALETTE["crt_cyan"], outline="") for _ in range(self.num_particles)]

    def update(self, t_now, bpm, ecg_val, is_flatline, sedentary_mins):
        cx, cy = self.cx, self.cy

        if is_flatline:
            self.canvas.itemconfig(self.id_core_body, outline=PALETTE["border_normal"])
            self.canvas.itemconfig(self.id_core_center, fill=PALETTE["red_alarm"])
            core_r, center_r = 12.0, 4.0
        else:
            accent_col = PALETTE["amber"] if sedentary_mins >= 45 else (PALETTE["crt_green"] if bpm < 95 else PALETTE["amber"])
            self.canvas.itemconfig(self.id_core_body, outline=PALETTE["crt_cyan"])
            self.canvas.itemconfig(self.id_core_center, fill=accent_col)

            # Expansión sistólica instantánea
            expansion = max(0.0, ecg_val) * 4.0
            core_r = 12.0 + expansion
            center_r = 5.0 + (expansion * 0.5)

        self.canvas.coords(self.id_core_body, cx - core_r, cy - core_r, cx + core_r, cy + core_r)
        self.canvas.coords(self.id_core_center, cx - center_r, cy - center_r, cx + center_r, cy + center_r)

        # Rotación suave y calmada de 14 partículas
        rot_speed = 18.0 + ((bpm - 60.0) * 0.3)
        base_angle = (t_now * rot_speed) % 360.0
        orbit_r = 22.0

        for i, p_id in enumerate(self.particles):
            angle = base_angle + (i * (360.0 / self.num_particles))
            rad = math.radians(angle)
            px = cx + math.cos(rad) * orbit_r
            py = cy + math.sin(rad) * orbit_r
            p_size = 1.2 if (i % 2 == 0) else 1.6
            self.canvas.coords(p_id, px - p_size, py - p_size, px + p_size, py + p_size)


# ── MÓDULO 2: STICKMAN RAGDOLL FÍSICO ARTICULADO ────────────────────────
class PhysicsRagdollStickman:
    def __init__(self, canvas, w=60, h=54):
        self.canvas = canvas
        self.w, self.h = w, h
        self.x = w / 2.0
        self.y = h - 12
        self.vx = 0.0
        self.vy = 0.0
        self.anim_t = 0.0

        self.id_floor = canvas.create_line(4, h - 8, w - 4, h - 8, fill="#18232D", width=1)
        self.id_head = canvas.create_oval(0, 0, 0, 0, fill=PALETTE["crt_green"], outline="")
        self.id_body = canvas.create_line(0, 0, 0, 0, fill=PALETTE["crt_green"], width=2)
        self.id_arm_l = canvas.create_line(0, 0, 0, 0, fill=PALETTE["crt_green"], width=1.5)
        self.id_arm_r = canvas.create_line(0, 0, 0, 0, fill=PALETTE["crt_green"], width=1.5)
        self.id_leg_l = canvas.create_line(0, 0, 0, 0, fill=PALETTE["crt_green"], width=1.5)
        self.id_leg_r = canvas.create_line(0, 0, 0, 0, fill=PALETTE["crt_green"], width=1.5)
        self.id_zzz = canvas.create_text(-100, -100, text="z Z", fill=PALETTE["amber"], font=("Consolas", 7, "bold"))

    def trigger_jump(self):
        self.vy = -6.0
        self.vx = random.choice([-1.5, 1.5])

    def update(self, dt, bpm, walking_speed, sedentary_mins, ecg_val):
        self.anim_t += dt * (bpm / 60.0) * 8.0

        self.vy += 16.0 * dt
        self.x += self.vx
        self.y += self.vy
        self.vx *= 0.92

        ground = self.h - 12
        if self.y >= ground:
            self.y = ground
            self.vy = 0.0

        if self.x < 8: self.x = 8; self.vx = abs(self.vx) * 0.7
        elif self.x > self.w - 8: self.x = self.w - 8; self.vx = -abs(self.vx) * 0.7

        color = PALETTE["amber"] if sedentary_mins >= 45 else PALETTE["crt_green"]
        self.canvas.itemconfig(self.id_head, fill=color)
        for item in [self.id_body, self.id_arm_l, self.id_arm_r, self.id_leg_l, self.id_leg_r]:
            self.canvas.itemconfig(item, fill=color)

        # 1. Modo Siesta Sedentaria
        if sedentary_mins >= 45 and walking_speed == 0.0 and self.y >= ground - 1:
            hx, hy = self.x - 4, ground - 8
            hr = 3.5
            self.canvas.coords(self.id_head, hx - hr, hy - hr, hx + hr, hy + hr)
            self.canvas.coords(self.id_body, hx, hy + hr, hx + 4, ground)
            self.canvas.coords(self.id_arm_l, hx, hy + 4, hx - 3, ground - 1)
            self.canvas.coords(self.id_arm_r, hx + 2, hy + 4, hx + 6, ground - 1)
            self.canvas.coords(self.id_leg_l, hx + 4, ground, hx + 10, ground)
            self.canvas.coords(self.id_leg_r, hx + 4, ground, hx + 8, ground + 1)
            zzz_y = hy - 6 - math.sin(time.time() * 2.5) * 2.5
            self.canvas.coords(self.id_zzz, hx + 6, zzz_y)
            return

        self.canvas.coords(self.id_zzz, -100, -100)

        # 2. Modo De Pie / Corriendo
        x, y = self.x, self.y
        head_r = 3.5
        body_len = 11

        beat_bounce = max(0.0, ecg_val) * 2.5
        cur_y = y - beat_bounce

        self.canvas.coords(self.id_head, x - head_r, cur_y - body_len - head_r*2, x + head_r, cur_y - body_len)
        self.canvas.coords(self.id_body, x, cur_y - body_len, x, cur_y)

        if walking_speed > 0 or self.y < ground - 1:
            leg_swing = math.sin(self.anim_t) * 6.0
            self.canvas.coords(self.id_leg_l, x, cur_y, x - leg_swing, ground)
            self.canvas.coords(self.id_leg_r, x, cur_y, x + leg_swing, ground)
            arm_swing = math.cos(self.anim_t) * 5.0
            self.canvas.coords(self.id_arm_l, x, cur_y - body_len + 3, x - arm_swing, cur_y - 2)
            self.canvas.coords(self.id_arm_r, x, cur_y - body_len + 3, x + arm_swing, cur_y - 2)
        else:
            self.canvas.coords(self.id_leg_l, x, cur_y, x - 4, ground)
            self.canvas.coords(self.id_leg_r, x, cur_y, x + 4, ground)
            self.canvas.coords(self.id_arm_l, x, cur_y - body_len + 3, x - 5, cur_y - 2)
            self.canvas.coords(self.id_arm_r, x, cur_y - body_len + 3, x + 5, cur_y - 2)


# ── Aplicación Master Unificada (390 x 180 px) ──────────────────────────
class MasterUnifiedHUD(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=PALETTE["bg_base"])

        # Dimensiones Perfectas para que TODO quepa holgadamente (390 x 180 px)
        self.hud_w = 390
        self.hud_h = 180
        screen_w = self.winfo_screenwidth()
        pos_x = screen_w - self.hud_w - 20
        pos_y = 60
        self.geometry(f"{self.hud_w}x{self.hud_h}+{pos_x}+{pos_y}")

        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.do_drag)

        self.data_queue = queue.Queue()
        self.cmd_queue = queue.Queue()

        self.telemetry = {
            "bpm": 72,
            "target_bpm": 72,
            "battery": 16,
            "steps": 2934,
            "spo2": 98,
            "sedentary_seconds": 59 * 60,
            "flash_edge_time": 0.0,
            "walking_speed": 0.0,
            "last_step_rx": 0.0
        }

        self.ecg_engine = SyntheticECGGenerator(buffer_len=120)

        self.setup_ui()

        # Instanciar Núcleo Halo y Stickman simultáneamente
        self.halo_core = IndustrialHaloCore(self.canvas_halo, cx=36, cy=32)
        self.stickman = PhysicsRagdollStickman(self.canvas_stickman, w=58, h=54)

        # Iniciar BLE
        self.ble_worker = BLETelemetryBridge(self.data_queue, self.cmd_queue)
        self.ble_worker.start()

        self.last_loop_time = time.perf_counter()
        self.after(16, self.render_60fps_loop)
        self.after(60, self.process_telemetry_queue)
        self.after(1000, self.update_sedentary_state)

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

    def setup_ui(self):
        self.container = ctk.CTkFrame(self, fg_color=PALETTE["bg_base"], corner_radius=8,
                                      border_width=1, border_color=PALETTE["border_normal"])
        self.container.pack(fill="both", expand=True, padx=1, pady=1)

        # ── 1. Header (0x1DBC · 🛡 · ⚡ 16% · [x]) ──
        self.header = ctk.CTkFrame(self.container, fg_color="transparent", height=22)
        self.header.pack(fill="x", padx=10, pady=(5, 1))

        self.lbl_id = ctk.CTkLabel(self.header, text="0x1DBC", font=("Consolas", 10, "bold"),
                                   text_color=PALETTE["crt_cyan"])
        self.lbl_id.pack(side="left")

        self.lbl_shield = ctk.CTkLabel(self.header, text=" 🛡", font=("Segoe UI", 10),
                                       text_color=PALETTE["crt_green"])
        self.lbl_shield.pack(side="left", padx=4)

        self.btn_close = ctk.CTkButton(self.header, text="x", width=14, height=14, corner_radius=2,
                                       fg_color="transparent", hover_color=PALETTE["bg_card"],
                                       text_color=PALETTE["text_sub"], font=("Segoe UI", 9),
                                       command=self.close_hud)
        self.btn_close.pack(side="right")

        self.lbl_bat = ctk.CTkLabel(self.header, text=f"⚡ {self.telemetry['battery']}%",
                                    font=("Consolas", 10, "bold"), text_color=PALETTE["text_sub"])
        self.lbl_bat.pack(side="right", padx=(0, 6))

        # Divisor Superior
        self.div_top = ctk.CTkFrame(self.container, fg_color=PALETTE["border_normal"], height=1)
        self.div_top.pack(fill="x", padx=8, pady=(2, 2))

        # ── 2. Módulo Central Triple (Halo Core + Stickman + ECG Oscilloscope) ──
        self.center_frame = ctk.CTkFrame(self.container, fg_color="transparent", height=88)
        self.center_frame.pack(fill="x", padx=8, pady=0)

        # Columna 1: Halo Core / Corazón Reactor + Número HERO
        self.col_halo = ctk.CTkFrame(self.center_frame, fg_color="transparent", width=80)
        self.col_halo.pack(side="left", padx=(0, 4))

        self.canvas_halo = tk.Canvas(self.col_halo, width=72, height=54,
                                     bg=PALETTE["bg_base"], highlightthickness=0)
        self.canvas_halo.pack(side="top", pady=(0, 0))

        self.lbl_hero_bpm = ctk.CTkLabel(self.col_halo, text="72",
                                         font=("Consolas", 22, "bold"),
                                         text_color=PALETTE["crt_green"])
        self.lbl_hero_bpm.pack(side="top", pady=(0, 0))

        # Columna 2: Stickman Ragdoll Físico
        self.col_stick = ctk.CTkFrame(self.center_frame, fg_color="transparent", width=62)
        self.col_stick.pack(side="left", padx=(0, 4))

        self.canvas_stickman = tk.Canvas(self.col_stick, width=58, height=54,
                                         bg=PALETTE["bg_base"], highlightthickness=0)
        self.canvas_stickman.pack(side="top", pady=(0, 0))

        self.lbl_stick_tag = ctk.CTkLabel(self.col_stick, text="RAGDOLL",
                                          font=("Consolas", 8, "bold"),
                                          text_color=PALETTE["text_sub"])
        self.lbl_stick_tag.pack(side="top", pady=(2, 0))

        # Columna 3: Canvas Osciloscopio ECG Grande
        self.canvas_ecg = tk.Canvas(self.center_frame, width=210, height=80,
                                    bg=PALETTE["bg_card"], highlightthickness=1,
                                    highlightbackground=PALETTE["border_normal"])
        self.canvas_ecg.pack(side="left", fill="both", expand=True)

        # Divisor Inferior
        self.div_bot = ctk.CTkFrame(self.container, fg_color=PALETTE["border_normal"], height=1)
        self.div_bot.pack(fill="x", padx=8, pady=(3, 3))

        # ── 3. Fila de Instrumentación Pura (Iconos + Métricas Grandes) ──
        self.pills_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.pills_frame.pack(fill="x", padx=8, pady=(0, 5))

        self.pill_time = ctk.CTkFrame(self.pills_frame, fg_color=PALETTE["bg_pill"],
                                      border_width=1, border_color=PALETTE["border_normal"],
                                      corner_radius=4, height=26)
        self.pill_time.pack(side="left", fill="x", expand=True, padx=(0, 3))

        self.lbl_time = ctk.CTkLabel(self.pill_time, text="⏱ 59m",
                                     font=("Consolas", 11, "bold"), text_color=PALETTE["amber"])
        self.lbl_time.pack(padx=6, pady=2)

        self.pill_spo2 = ctk.CTkFrame(self.pills_frame, fg_color=PALETTE["bg_pill"],
                                      border_width=1, border_color=PALETTE["border_normal"],
                                      corner_radius=4, height=26)
        self.pill_spo2.pack(side="left", fill="x", expand=True, padx=3)

        self.lbl_spo2 = ctk.CTkLabel(self.pill_spo2, text="🫁 98%",
                                     font=("Consolas", 11, "bold"), text_color=PALETTE["crt_cyan"])
        self.lbl_spo2.pack(padx=6, pady=2)

        self.pill_steps = ctk.CTkFrame(self.pills_frame, fg_color=PALETTE["bg_pill"],
                                       border_width=1, border_color=PALETTE["border_normal"],
                                       corner_radius=4, height=26)
        self.pill_steps.pack(side="left", fill="x", expand=True, padx=(3, 0))

        self.lbl_steps = ctk.CTkLabel(self.pill_steps, text="👟 2.9k",
                                      font=("Consolas", 11, "bold"), text_color=PALETTE["text_hero"])
        self.lbl_steps.pack(padx=6, pady=2)

    # ── Osciloscopio con Parallax y Flatline ──
    def draw_ecg_oscilloscope(self, buffer, is_flatline, parallax_off):
        self.canvas_ecg.delete("all")
        w = 210
        h = 80
        mid_y = h / 2.0

        for gx in range(-20, w + 20, 20):
            x_line = gx + parallax_off
            self.canvas_ecg.create_line(x_line, 0, x_line, h, fill=PALETTE["ecg_grid"], width=1)
        for gy in range(0, h, 14):
            self.canvas_ecg.create_line(0, gy, w, gy, fill=PALETTE["ecg_grid"], width=1)

        trace_col = PALETTE["red_alarm"] if is_flatline else PALETTE["crt_green"]

        step = w / float(len(buffer) - 1)
        coords = []
        for i, val in enumerate(buffer):
            x = i * step
            y = mid_y - (val * (mid_y - 6))
            coords.append((x, y))

        for i in range(len(coords) - 1):
            x1, y1 = coords[i]
            x2, y2 = coords[i+1]
            self.canvas_ecg.create_line(x1, y1, x2, y2, fill=trace_col, width=1.5)

        lx, ly = coords[-1]
        cursor_col = PALETTE["red_alarm"] if is_flatline else "#FFFFFF"
        self.canvas_ecg.create_oval(lx - 2, ly - 2, lx + 2, ly + 2, fill=cursor_col, outline=PALETTE["crt_cyan"])

    # ── Loop a 60 FPS Locked ──
    def render_60fps_loop(self):
        t_now = time.perf_counter()
        dt = t_now - self.last_loop_time
        self.last_loop_time = t_now

        # 1. ECG
        ecg_buf, curr_bpm, is_flat = self.ecg_engine.step(dt, self.telemetry["walking_speed"])
        self.draw_ecg_oscilloscope(ecg_buf, is_flat, self.ecg_engine.parallax_offset)

        # 2. Halo Core Reactor (14 partículas) & Stickman Ragdoll simultáneos
        mins_sed = self.telemetry["sedentary_seconds"] // 60
        latest_ecg = ecg_buf[-1]

        self.halo_core.update(t_now, curr_bpm, latest_ecg, is_flat, mins_sed)
        self.stickman.update(dt, curr_bpm, self.telemetry["walking_speed"], mins_sed, latest_ecg)

        # 3. Número HERO
        if is_flat:
            self.lbl_hero_bpm.configure(text="--", text_color=PALETTE["red_alarm"])
        else:
            col = PALETTE["crt_green"] if curr_bpm < 95 else PALETTE["amber"]
            self.lbl_hero_bpm.configure(text=str(int(curr_bpm)), text_color=col)

        # 4. Glitch / Destello
        if t_now - self.telemetry["flash_edge_time"] < 0.12:
            self.container.configure(border_color=PALETTE["border_flash"])
        elif mins_sed >= 60:
            pulse = math.sin(t_now * 4.0)
            b_col = PALETTE["border_red"] if pulse > 0.3 else PALETTE["border_amber"]
            self.container.configure(border_color=b_col)
        elif mins_sed >= 45:
            self.container.configure(border_color=PALETTE["border_amber"])
        else:
            self.container.configure(border_color=PALETTE["border_normal"])

        self.after(16, self.render_60fps_loop)

    # ── Telemetría BLE ──
    def process_telemetry_queue(self):
        try:
            while not self.data_queue.empty():
                pkt = self.data_queue.get_nowait()
                p_type = pkt.get("type")
                t_rx = pkt.get("t", time.perf_counter())

                self.telemetry["flash_edge_time"] = t_rx

                if p_type == "live_hr":
                    bpm = pkt.get("value")
                    self.telemetry["target_bpm"] = bpm
                    self.ecg_engine.set_target_bpm(bpm)

                elif p_type == "battery":
                    pct = pkt.get("value")
                    self.telemetry["battery"] = pct
                    self.lbl_bat.configure(text=f"⚡ {pct}%")

                elif p_type == "spo2":
                    val = pkt.get("value")
                    self.telemetry["spo2"] = val
                    self.lbl_spo2.configure(text=f"🫁 {val}%")

                elif p_type == "activity":
                    steps = pkt.get("steps")
                    self.telemetry["steps"] = steps
                    if steps >= 1000:
                        self.lbl_steps.configure(text=f"👟 {steps/1000.0:.1f}k")
                    else:
                        self.lbl_steps.configure(text=f"👟 {steps}")

                elif p_type == "step_inc":
                    self.telemetry["sedentary_seconds"] = 0
                    self.telemetry["walking_speed"] = 1.0
                    self.telemetry["last_step_rx"] = time.perf_counter()
                    self.stickman.trigger_jump()

        except queue.Empty:
            pass

        if time.perf_counter() - self.telemetry["last_step_rx"] > 2.0:
            self.telemetry["walking_speed"] = 0.0

        self.after(60, self.process_telemetry_queue)

    def update_sedentary_state(self):
        self.telemetry["sedentary_seconds"] += 1
        mins = self.telemetry["sedentary_seconds"] // 60

        if mins >= 45:
            self.lbl_time.configure(text=f"⏱ {mins}m", text_color=PALETTE["amber"])
            self.pill_time.configure(border_color=PALETTE["border_amber"])
        else:
            self.lbl_time.configure(text=f"⏱ {mins}m", text_color=PALETTE["crt_green"])
            self.pill_time.configure(border_color=PALETTE["border_normal"])

        self.after(1000, self.update_sedentary_state)


if __name__ == "__main__":
    app = MasterUnifiedHUD()
    app.mainloop()
