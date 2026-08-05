"""
JARVIS BIOMETRIC HUD — Smartwatch Air5 BLE Ultra-Smooth 60 FPS Edition
- Láser sincronizado con el envío de paquetes BLE reales.
- Halo animado de batería (Halo exterior: Batería reloj, Halo interior: Batería corporal).
- Motor de Insights Inteligentes Contextuales (Alerta de estrés mental, ventana de rendimiento, fatiga).
- Simulador fisiológico de pulso cardíaco para pruebas y demostración instantánea.
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

# Paleta exacta de la referencia (Deep Navy / Cyber Red / Phosphor Cyan / Slate)
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
    "bar_fill": "#E2E8F0",
    "halo_battery": "#E2E8F0",
    "halo_body": "#38BDF8",
    "halo_track": "#1B2636"
}

# ── Hilo de Conexion BLE WinRT Robusto ───────────────────────────────────
class BLEBridgeThread(threading.Thread):
    def __init__(self, data_queue, cmd_queue, mac_addr=MAC_ADDR):
        super().__init__(daemon=True)
        self.data_queue = data_queue
        self.cmd_queue = cmd_queue
        self.mac_addr = mac_addr
        self.running = True
        self.simulation_mode = False

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
            self.data_queue.put({"type": "status", "status": "simulating", "msg": "MODO_SIMULADOR"})
            return

        mac_int = int(self.mac_addr.replace(":", ""), 16)

        while self.running:
            self.data_queue.put({"type": "status", "status": "connecting", "msg": "BUSCANDO_BLE"})
            try:
                device = await BluetoothLEDevice.from_bluetooth_address_async(mac_int)
                if not device:
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "SIN_DISPOSITIVO"})
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


# ── Motor del Núcleo HAL 9000 con Halo de Batería & Láser de Paquete ─────
class JarvisCoreWithHalo:
    """Núcleo cibernético reactivo a 60 FPS con Halo de batería circular y láser sincronizado a paquetes BLE."""
    def __init__(self, canvas, cx=44, cy=44):
        self.canvas = canvas
        self.cx = cx
        self.cy = cy
        self.bpm = 72
        self.smoothed_bpm = 72.0
        self.battery_pct = 16
        self.body_battery_pct = 75

        # Estado del Láser de Sincronización
        self.laser_sweep_start = 0.0
        self.laser_active = False

        # 1. Pistas de fondo de Halos de Batería
        self.id_halo_track_outer = canvas.create_oval(cx-41, cy-41, cx+41, cy+41, outline=HUD_THEME["halo_track"], width=2.5)
        self.id_halo_track_inner = canvas.create_oval(cx-36, cy-36, cx+36, cy+36, outline=HUD_THEME["halo_track"], width=1.5)

        # 2. Arcos activos de Halo (Batería Reloj Exterior & Batería Corporal Interior)
        self.id_halo_watch = canvas.create_arc(cx-41, cy-41, cx+41, cy+41, start=90, extent=-57,
                                               outline=HUD_THEME["halo_battery"], width=2.5, style="arc")
        self.id_halo_body = canvas.create_arc(cx-36, cy-36, cx+36, cy+36, start=90, extent=-270,
                                              outline=HUD_THEME["halo_body"], width=1.5, style="arc")

        # 3. Anillos de Carcasa Metálica
        self.id_ring3 = canvas.create_oval(cx-32, cy-32, cx+32, cy+32, fill="#0F1622", outline="#1F2D40", width=1.5)
        self.id_ring2 = canvas.create_oval(cx-26, cy-26, cx+26, cy+26, fill="#0A0F18", outline="#162232", width=1)
        self.id_ring1 = canvas.create_oval(cx-20, cy-20, cx+20, cy+20, fill="#06090E", outline="#111A26", width=1)

        # 4. Resplandores Dinámicos
        self.id_glow_outer = canvas.create_oval(cx-16, cy-16, cx+16, cy+16, fill="#2A0B12", outline="")
        self.id_glow_mid = canvas.create_oval(cx-13, cy-13, cx+13, cy+13, fill="#4C0D1A", outline="")

        # 5. Núcleo e Iris Rojo
        self.id_core = canvas.create_oval(cx-10, cy-10, cx+10, cy+10, fill="#E11D48", outline="#FF4A5A", width=1.5)
        self.id_center = canvas.create_oval(cx-5, cy-5, cx+5, cy+5, fill="#FF6B7A", outline="#FFA4AD", width=1)

        # 6. Hendiduras Horizontales (Slits)
        self.id_slits = [canvas.create_line(0, 0, 0, 0, fill="#0B0F16", width=1.5) for _ in range(5)]

        # 7. Rayo Láser de Sincronización Real (Atraviesa con cada paquete BLE recibido)
        self.id_laser = canvas.create_line(0, 0, 0, 0, fill="#FFFFFF", width=2)
        self.id_laser_glow = canvas.create_line(0, 0, 0, 0, fill="#FF4A5A", width=4)

        # 8. Partículas Orbitales de Velocidad Dinámica
        self.id_orbit = canvas.create_oval(0, 0, 0, 0, fill="#FF4A5A", outline="")

    def set_bpm(self, bpm):
        self.bpm = max(40, min(190, bpm))

    def set_battery(self, watch_pct, body_pct):
        self.battery_pct = watch_pct
        self.body_battery_pct = body_pct
        # Actualizar arcos de Halo
        extent_watch = - (watch_pct / 100.0) * 359.9
        extent_body = - (body_pct / 100.0) * 359.9
        self.canvas.itemconfig(self.id_halo_watch, extent=extent_watch)
        self.canvas.itemconfig(self.id_halo_body, extent=extent_body)

    def trigger_packet_sweep(self):
        """Activa el barrido láser de confirmación cuando llega un paquete de datos real."""
        self.laser_sweep_start = time.perf_counter()
        self.laser_active = True

    def update_frame(self, t_now):
        cx, cy = self.cx, self.cy

        # Suavizado de BPM
        self.smoothed_bpm += (self.bpm - self.smoothed_bpm) * 0.08
        freq = self.smoothed_bpm / 60.0

        # Curva de Latido Fisiológico Asimétrico
        phase = (t_now * freq * 2.0 * math.pi) % (2.0 * math.pi)
        pulse = max(0.0, math.sin(phase)) ** 1.8

        glow_r = 13.0 + (pulse * 5.5)
        core_r = 9.5 + (pulse * 3.0)
        center_r = 4.0 + (pulse * 1.8)

        # 1. Resplandores y Núcleo
        self.canvas.coords(self.id_glow_outer, cx - glow_r - 4, cy - glow_r - 4, cx + glow_r + 4, cy + glow_r + 4)
        self.canvas.coords(self.id_glow_mid, cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r)
        self.canvas.coords(self.id_core, cx - core_r, cy - core_r, cx + core_r, cy + core_r)
        self.canvas.coords(self.id_center, cx - center_r, cy - center_r, cx + center_r, cy + center_r)

        # 2. Hendiduras
        slit_offsets = [-6.0, -3.0, 0.0, 3.0, 6.0]
        for i, y_off in enumerate(slit_offsets):
            if abs(y_off) < core_r:
                slit_w = math.sqrt(max(0.0, core_r**2 - y_off**2))
                self.canvas.coords(self.id_slits[i], cx - slit_w, cy + y_off, cx + slit_w, cy + y_off)
            else:
                self.canvas.coords(self.id_slits[i], 0, 0, 0, 0)

        # 3. Rayo Láser Sincronizado a Paquetes (Barrido en 0.28s al recibir datos)
        if self.laser_active:
            elapsed = t_now - self.laser_sweep_start
            duration = 0.28
            if elapsed < duration:
                progress = elapsed / duration  # 0.0 a 1.0
                scan_y = cy - core_r + (progress * (core_r * 2))
                scan_w = math.sqrt(max(0.0, core_r**2 - (scan_y - cy)**2))
                self.canvas.coords(self.id_laser, cx - scan_w, scan_y, cx + scan_w, scan_y)
                self.canvas.coords(self.id_laser_glow, cx - scan_w - 1, scan_y, cx + scan_w + 1, scan_y)
            else:
                self.laser_active = False
                self.canvas.coords(self.id_laser, 0, 0, 0, 0)
                self.canvas.coords(self.id_laser_glow, 0, 0, 0, 0)

        # 4. Partícula Orbital (Velocidad acelerada proporcionalmente al BPM)
        orb_speed = self.smoothed_bpm * 2.2
        orb_angle = (t_now * orb_speed) % 360.0
        rad = math.radians(orb_angle)
        ox = cx + math.cos(rad) * 23.0
        oy = cy + math.sin(rad) * 23.0
        self.canvas.coords(self.id_orbit, ox - 1.5, oy - 1.5, ox + 1.5, oy + 1.5)


# ── Motor de Insights Fisiológicos Inteligentes ─────────────────────────
class BiometricInsightEngine:
    """Motor de análisis cruzado que interpreta la telemetría en insights humanos de alta relevancia."""
    def __init__(self):
        self.current_insight = "SISTEMA_ESTABLE  ·  Monitoreo cardíaco activo"
        self.is_critical = False

    def evaluate(self, hr, hrv, steps_diff, sedentary_mins, body_battery):
        # 1. Alerta de sobre-esfuerzo silencioso (FC alta + 0 pasos)
        if hr >= 92 and steps_diff == 0:
            self.is_critical = True
            return f"ESTRÉS MENTAL DETECTADO  ·  Pulso {hr} LPM sin movimiento físico"

        # 2. Aviso de sedentarismo con contexto fisiológico
        if sedentary_mins >= 45:
            self.is_critical = True
            return f"CIRCULACIÓN LENTA  ·  {sedentary_mins}m sentado. Tu FC basal cayó a niveles de letargo"

        # 3. Alerta de energía corporal baja
        if body_battery < 25:
            self.is_critical = True
            return f"BATERÍA CORPORAL {body_battery}%  ·  Se recomienda pausa de recuperación activa"

        # 4. Ventana de mejor rendimiento cognitivo (HRV alto y estable)
        if hrv >= 45 and hr < 80:
            self.is_critical = False
            return "VENTANA DE ALTO RENDIMIENTO  ·  HRV óptimo para tareas de enfoque profundo"

        # 5. Estado normal de equilibrio
        self.is_critical = False
        return "SISTEMA EN EQUILIBRIO  ·  Ritmo y oxigenación estables"


# ── Aplicacion Principal: Jarvis HUD 60 FPS con Simulador ──────────────
class JarvisHUDApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Ventana Superpuesta Frameless
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=HUD_THEME["bg_window"])

        self.width_full = 336
        self.height_full = 260
        self.is_mini = False
        self.simulation_active = False

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
            "hrv": 42.5,
            "stress_pct": 0.28,
            "battery": 16,
            "body_battery": 78,
            "steps": 2934,
            "last_steps": 2934,
            "spo2": 98,
            "system_status": "SISTEMA_OK",
            "sedentary_seconds": 50 * 60,
            "last_packet_time": time.time()
        }

        # Motores de Inteligencia
        self.insight_engine = BiometricInsightEngine()

        # Construir Interfaz
        self.setup_ui()

        # Inicializar Motor Reactor con Halos y Láser
        self.reactor = JarvisCoreWithHalo(self.canvas_reactor, cx=44, cy=44)
        self.reactor.set_battery(self.metrics["battery"], self.metrics["body_battery"])

        # Iniciar Worker BLE en background
        self.ble_thread = BLEBridgeThread(self.data_queue, self.cmd_queue)
        self.ble_thread.start()

        # Ciclo de 60 FPS locked (~16ms)
        self.last_anim_time = time.perf_counter()
        self.after(16, self.render_60fps_loop)

        # Ciclos de datos desacoplados
        self.after(80, self.process_ble_queue_loop)
        self.after(1000, self.update_sedentary_and_insights_loop)

    def start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def do_drag(self, event):
        x = self.winfo_x() + (event.x - self._drag_x)
        y = self.winfo_y() + (event.y - self._drag_y)
        self.geometry(f"+{x}+{y}")

    def toggle_simulation(self):
        """Alterna el simulador fisiológico de pulso cardíaco para pruebas."""
        self.simulation_active = not self.simulation_active
        if self.simulation_active:
            self.lbl_status.configure(text="SIMULADOR_ON", text_color="#F59E0B")
        else:
            self.lbl_status.configure(text="SISTEMA_OK", text_color=HUD_THEME["cyan_ok"])

    def toggle_mini_mode(self, event=None):
        self.is_mini = not self.is_mini
        if self.is_mini:
            self.geometry(f"195x105")
            self.header.pack_forget()
            self.banner_insight.pack_forget()
            self.sep_line.pack_forget()
            self.row_stress.pack_forget()
            self.row_body_bat.pack_forget()
            self.footer.pack_forget()
        else:
            self.geometry(f"{self.width_full}x{self.height_full}")
            self.container.pack_forget()
            self.container.pack(fill="both", expand=True, padx=1, pady=1)
            self.header.pack(fill="x", padx=14, pady=(10, 4))
            self.banner_insight.pack(fill="x", padx=14, pady=(2, 6))
            self.body_frame.pack(fill="both", expand=True, padx=14, pady=(0, 4))
            self.sep_line.pack(fill="x", pady=(0, 4))
            self.row_stress.pack(fill="x", pady=2)
            self.row_body_bat.pack(fill="x", pady=2)
            self.footer.pack(fill="x", padx=14, pady=(0, 8))

    def close_hud(self):
        self.destroy()
        sys.exit(0)

    def setup_ui(self):
        # Marco Contenedor Principal
        self.container = ctk.CTkFrame(self, fg_color=HUD_THEME["bg_window"], corner_radius=14,
                                      border_width=1, border_color=HUD_THEME["border_window"])
        self.container.pack(fill="both", expand=True, padx=1, pady=1)

        # ── 1. Header (• EN VIVO / JARVIS ... [SIM] SISTEMA_OK) ──
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

        # SISTEMA_OK (Clickeable para activar Simulador)
        self.lbl_status = ctk.CTkButton(self.header, text="SISTEMA_OK", width=75, height=20, corner_radius=4,
                                        fg_color="transparent", hover_color=HUD_THEME["bg_card"],
                                        text_color=HUD_THEME["cyan_ok"], font=("Consolas", 10, "bold"),
                                        command=self.toggle_simulation)
        self.lbl_status.pack(side="right")

        # ── 2. Banner de Insights Inteligentes Contextuales ──
        self.banner_insight = ctk.CTkFrame(self.container, fg_color=HUD_THEME["red_banner_bg"], corner_radius=6,
                                           border_width=1, border_color=HUD_THEME["red_banner_border"], height=28)
        self.banner_insight.pack(fill="x", padx=14, pady=(2, 6))

        self.lbl_insight_text = ctk.CTkLabel(self.banner_insight, text="¡MUÉVETE!  50 min sentado",
                                             font=("Consolas", 8, "bold"), text_color=HUD_THEME["red_alert"])
        self.lbl_insight_text.pack(side="left", padx=8, pady=4)

        self.lbl_insight_icon = ctk.CTkLabel(self.banner_insight, text="/!\\", font=("Consolas", 8, "bold"),
                                             text_color=HUD_THEME["red_alert"])
        self.lbl_insight_icon.pack(side="right", padx=8, pady=4)

        # ── 3. Cuerpo Central (Reactor con Halo + Métricas) ──
        self.body_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.body_frame.pack(fill="both", expand=True, padx=14, pady=(0, 4))

        # Reactor HAL 9000 con Halo de Batería Circular
        self.canvas_reactor = tk.Canvas(self.body_frame, width=88, height=88,
                                        bg=HUD_THEME["bg_window"], highlightthickness=0)
        self.canvas_reactor.pack(side="left", padx=(0, 10), pady=0)

        # Panel de Estadísticas
        self.stats_frame = ctk.CTkFrame(self.body_frame, fg_color="transparent")
        self.stats_frame.pack(side="left", fill="both", expand=True)

        # PULSO_VIVO 72 LPM
        self.row_hr = ctk.CTkFrame(self.stats_frame, fg_color="transparent")
        self.row_hr.pack(fill="x", pady=(0, 1))

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
        self.sep_line.pack(fill="x", pady=(0, 4))

        # ESTRÉS
        self.row_stress = ctk.CTkFrame(self.stats_frame, fg_color="transparent")
        self.row_stress.pack(fill="x", pady=1)

        self.lbl_stress_tag = ctk.CTkLabel(self.row_stress, text="ESTRÉS", width=55, anchor="w",
                                          font=("Consolas", 9), text_color=HUD_THEME["text_muted"])
        self.lbl_stress_tag.pack(side="left")

        self.bar_stress = ctk.CTkProgressBar(self.row_stress, height=6, width=95,
                                             progress_color=HUD_THEME["bar_fill"],
                                             fg_color=HUD_THEME["bar_track"])
        self.bar_stress.pack(side="left", padx=6)
        self.bar_stress.set(0.28)

        self.lbl_stress_val = ctk.CTkLabel(self.row_stress, text="BAJO", width=38, anchor="e",
                                          font=("Consolas", 9, "bold"), text_color=HUD_THEME["cyan_ok"])
        self.lbl_stress_val.pack(side="right")

        # BATERÍA CORPORAL (Body Battery)
        self.row_body_bat = ctk.CTkFrame(self.stats_frame, fg_color="transparent")
        self.row_body_bat.pack(fill="x", pady=1)

        self.lbl_bb_tag = ctk.CTkLabel(self.row_body_bat, text="ENERGÍA", width=55, anchor="w",
                                       font=("Consolas", 9), text_color=HUD_THEME["text_muted"])
        self.lbl_bb_tag.pack(side="left")

        self.bar_bb = ctk.CTkProgressBar(self.row_body_bat, height=6, width=95,
                                         progress_color=HUD_THEME["halo_body"],
                                         fg_color=HUD_THEME["bar_track"])
        self.bar_bb.pack(side="left", padx=6)
        self.bar_bb.set(0.78)

        self.lbl_bb_val = ctk.CTkLabel(self.row_body_bat, text="78%", width=38, anchor="e",
                                       font=("Consolas", 9, "bold"), text_color=HUD_THEME["text_hero"])
        self.lbl_bb_val.pack(side="right")

        # ── 4. Footer: TIEMPO SENTADO & Micro-Telemetría ──
        self.footer = ctk.CTkFrame(self.container, fg_color="transparent")
        self.footer.pack(fill="x", padx=14, pady=(0, 8))

        self.lbl_time_tag = ctk.CTkLabel(self.footer, text="TIEMPO SENTADO", font=("Consolas", 9),
                                         text_color=HUD_THEME["text_muted"])
        self.lbl_time_tag.pack(side="left")

        # Batería Reloj sutil (Halo indicator legend)
        self.lbl_bat_legend = ctk.CTkLabel(self.footer, text="[HALO: 16% BAT]", font=("Consolas", 8),
                                           text_color=HUD_THEME["text_muted"])
        self.lbl_bat_legend.pack(side="left", padx=10)

        self.lbl_time_val = ctk.CTkLabel(self.footer, text="50:00", font=("Consolas", 11, "bold"),
                                         text_color=HUD_THEME["red_alert"])
        self.lbl_time_val.pack(side="right")

    # ── Loop a 60 FPS (16.6ms) con Delta-Time Exacto ──
    def render_60fps_loop(self):
        t_now = time.perf_counter()

        # Si el simulador fisiológico está activo, generar oscilaciones orgánicas
        if self.simulation_active:
            # Simular arritmia sinusal respiratoria y micro-variaciones
            sim_bpm = int(72 + (math.sin(t_now * 0.4) * 8) + (math.sin(t_now * 2.1) * 3))
            self.metrics["hr"] = sim_bpm
            self.lbl_hr_num.configure(text=str(sim_bpm))
            # Simular pulso de paquete cada 1.5s
            if int(t_now * 10) % 15 == 0:
                self.reactor.trigger_packet_sweep()

        self.reactor.set_bpm(self.metrics["hr"])
        self.reactor.update_frame(t_now)
        self.after(16, self.render_60fps_loop)

    # ── Loop de Evaluación de Insights Contextuales y Sedentarismo ──
    def update_sedentary_and_insights_loop(self):
        self.metrics["sedentary_seconds"] += 1
        secs = self.metrics["sedentary_seconds"]
        mins = secs // 60
        rem_secs = secs % 60

        time_str = f"{mins:02d}:{rem_secs:02d}"
        self.lbl_time_val.configure(text=time_str)

        # Evaluar Insight Contextual con el Motor
        steps_diff = self.metrics["steps"] - self.metrics["last_steps"]
        insight_msg = self.insight_engine.evaluate(
            self.metrics["hr"],
            self.metrics["hrv"],
            steps_diff,
            mins,
            self.metrics["body_battery"]
        )

        self.lbl_insight_text.configure(text=insight_msg)
        if self.insight_engine.is_critical:
            self.banner_insight.configure(fg_color=HUD_THEME["red_banner_bg"], border_color=HUD_THEME["red_banner_border"])
            self.lbl_insight_text.configure(text_color=HUD_THEME["red_alert"])
            self.lbl_insight_icon.configure(text="/!\\", text_color=HUD_THEME["red_alert"])
            self.lbl_time_val.configure(text_color=HUD_THEME["red_alert"])
        else:
            self.banner_insight.configure(fg_color=HUD_THEME["blue_banner_bg"], border_color=HUD_THEME["blue_banner_border"])
            self.lbl_insight_text.configure(text_color=HUD_THEME["cyan_ok"])
            self.lbl_insight_icon.configure(text="[OK]", text_color=HUD_THEME["cyan_ok"])
            self.lbl_time_val.configure(text_color=HUD_THEME["text_hero"])

        self.after(1000, self.update_sedentary_and_insights_loop)

    # ── Loop de Telemetría BLE Asíncrona ──
    def process_ble_queue_loop(self):
        try:
            while not self.data_queue.empty():
                msg = self.data_queue.get_nowait()
                m_type = msg.get("type")

                # Cada paquete recibido dispara el láser visual en el orbe
                self.reactor.trigger_packet_sweep()

                if m_type == "status":
                    text = msg.get("msg")
                    if not self.simulation_active:
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

                    # Calcular variabilidad y nivel de estrés
                    self.metrics["hr_samples"].append(bpm)
                    if len(self.metrics["hr_samples"]) > 15:
                        self.metrics["hr_samples"].pop(0)

                    if len(self.metrics["hr_samples"]) >= 4:
                        diffs = [abs(self.metrics["hr_samples"][i] - self.metrics["hr_samples"][i-1])
                                 for i in range(1, len(self.metrics["hr_samples"]))]
                        mean_diff = sum(diffs) / len(diffs)
                        self.metrics["hrv"] = round((mean_diff * 9.5) + (110 - bpm) * 0.3, 1)

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
                    self.lbl_bat_legend.configure(text=f"[HALO: {pct}% BAT]")
                    self.reactor.set_battery(pct, self.metrics["body_battery"])

                elif m_type == "daily_activity":
                    steps = msg.get("steps")
                    if steps > self.metrics["last_steps"] + 15:
                        self.metrics["sedentary_seconds"] = 0
                        self.metrics["last_steps"] = steps

                elif m_type == "step_inc":
                    self.metrics["sedentary_seconds"] = 0

        except queue.Empty:
            pass

        self.after(80, self.process_ble_queue_loop)


if __name__ == "__main__":
    app = JarvisHUDApp()
    app.mainloop()
