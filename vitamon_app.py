"""
VITAMON — Minimalist Health Companion & Smartwatch Air5 BLE Widget
Diseno minimalista, sobrio y sin emojis.
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
from tkinter import messagebox
import customtkinter as ctk

# ── Configuracion Visual Minimalista ──────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

MAC_ADDR = "81:0A:B7:00:1D:BC"

# Paleta Minimalista Neutra (Matte Black / Charcoal / Soft White)
PALETTE = {
    "bg_main": "#121214",
    "card_bg": "#1A1A1E",
    "card_border": "#27272D",
    "divider": "#222228",
    "text_primary": "#F4F4F5",
    "text_secondary": "#A1A1AA",
    "text_muted": "#71717A",
    "accent": "#E4E4E7",
    "accent_subtle": "#3F3F46",
    "status_on": "#A1A1AA",
    "status_off": "#71717A",
}

# ── Hilo de Conexion BLE WinRT en Segundo Plano ─────────────────────────
class BLEBridgeThread(threading.Thread):
    def __init__(self, data_queue, cmd_queue, mac_addr=MAC_ADDR):
        super().__init__(daemon=True)
        self.data_queue = data_queue
        self.cmd_queue = cmd_queue
        self.mac_addr = mac_addr
        self.running = True
        self.connected = False

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
            self.data_queue.put({"type": "status", "status": "error", "msg": "WinRT no disponible"})
            return

        mac_int = int(self.mac_addr.replace(":", ""), 16)

        while self.running:
            self.data_queue.put({"type": "status", "status": "connecting", "msg": "Conectando"})
            try:
                device = await BluetoothLEDevice.from_bluetooth_address_async(mac_int)
                if not device:
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "No detectado"})
                    await asyncio.sleep(5)
                    continue

                services_res = await device.get_gatt_services_async()
                if services_res.status != GattCommunicationStatus.SUCCESS:
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "Sin servicios"})
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
                    self.data_queue.put({"type": "status", "status": "disconnected", "msg": "Canal no disponible"})
                    device.close()
                    await asyncio.sleep(4)
                    continue

                def on_ch1_notify(sender, args):
                    reader = DataReader.from_buffer(args.characteristic_value)
                    data = bytes([reader.read_byte() for _ in range(reader.unconsumed_buffer_length)])
                    if not data: return
                    cmd = data[0]

                    if cmd == 0xA2 and len(data) >= 2:
                        self.data_queue.put({"type": "battery", "value": data[1]})

                    elif cmd == 0xE5 and len(data) >= 4 and data[1] == 0x11:
                        bpm = data[3]
                        if 35 <= bpm <= 220:
                            self.data_queue.put({"type": "live_hr", "value": bpm})

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
                    await asyncio.sleep(0.1)

                # Handshake
                await send_cmd(write1, "0808442a01243943756ffffed921005f784be1dc")
                if write2:
                    await send_cmd(write2, "00f4000000000000000000000000000000000402")

                # Silenciar spam de sedentarismo
                await send_cmd(write1, "d1ff64")
                await send_cmd(write1, "d7160017000000")

                # Sincronizar hora
                now = datetime.now()
                time_hex = f"a3{now.year:04x}{now.month:02x}{now.day:02x}{now.hour:02x}{now.minute:02x}{now.second:02x}"
                await send_cmd(write1, time_hex)

                # Solicitar bateria y actividad inicial
                await send_cmd(write1, "a2")
                await send_cmd(write1, "2601")

                self.connected = True
                self.data_queue.put({"type": "status", "status": "connected", "msg": "Conectado"})

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

                    if time.time() - last_poll > 20:
                        last_poll = time.time()
                        await send_cmd(write1, "a2")
                        await send_cmd(write1, "2601")

                    await asyncio.sleep(0.5)

            except Exception as e:
                self.connected = False
                self.data_queue.put({"type": "status", "status": "disconnected", "msg": "Desconectado"})
                await asyncio.sleep(4)


# ── Criatura Minimalista (Mascota Zen en Canvas) ─────────────────────────
class MinimalCreature:
    """Criatura de lineas limpias, estetica sobria estilo ilustracion japonesa minimalista."""
    def __init__(self, canvas):
        self.canvas = canvas
        self.frame = 0
        self.state = "calm"  # calm, active, resting
        self.level = 1
        self.xp = 0
        self.stages = ["Semilla", "Brote", "Espiritu", "Guardian"]

    def update_stats(self, steps, hr):
        self.xp = steps
        self.level = max(1, 1 + steps // 1000)
        if hr > 100:
            self.state = "active"
        elif hr < 65 and steps == 0:
            self.state = "resting"
        else:
            self.state = "calm"

    def get_stage_name(self):
        idx = min(len(self.stages) - 1, (self.level - 1))
        return self.stages[idx]

    def render(self, cx=130, cy=75):
        self.canvas.delete("all")
        self.frame += 1
        f = self.frame

        # Respiracion sutil
        b_speed = 0.15 if self.state != "active" else 0.35
        b_amp = 3 if self.state != "active" else 5
        bounce = int(math.sin(f * b_speed) * b_amp)

        # Sombra sutil y discreta
        self.canvas.create_oval(cx - 26, cy + 34, cx + 26, cy + 40, fill="#16161A", outline="")

        # Cuerpo redondeado suave (Blanco calido sobre fondo oscuro)
        r = 28
        self.canvas.create_oval(cx - r, cy - r + bounce, cx + r, cy + r + bounce,
                                fill="#F4F4F5", outline="#E4E4E7", width=1)

        # Orejitas / Detalles sutiles segun nivel
        if self.level >= 2:
            # Orejitas pequenas y suaves
            self.canvas.create_oval(cx - 20, cy - 32 + bounce, cx - 10, cy - 20 + bounce, fill="#F4F4F5", outline="")
            self.canvas.create_oval(cx + 10, cy - 32 + bounce, cx + 20, cy - 20 + bounce, fill="#F4F4F5", outline="")
            if self.level >= 3:
                # Brote superior minimalista
                self.canvas.create_line(cx, cy - 28 + bounce, cx, cy - 38 + bounce, fill="#A1A1AA", width=2)
                self.canvas.create_oval(cx - 4, cy - 42 + bounce, cx + 4, cy - 36 + bounce, fill="#A1A1AA", outline="")

        # Ojos minimalistas
        if self.state == "resting":
            # Ojos cerrados serenos (arcos discretos)
            self.canvas.create_arc(cx - 15, cy - 8 + bounce, cx - 5, cy + 2 + bounce, start=0, extent=-180, fill="", outline="#27272D", width=2)
            self.canvas.create_arc(cx + 5, cy - 8 + bounce, cx + 15, cy + 2 + bounce, start=0, extent=-180, fill="", outline="#27272D", width=2)
        else:
            # Ojos de puntos limpios con parpadeo natural
            is_blink = (f % 70 > 66)
            eye_h = 1 if is_blink else 4
            self.canvas.create_oval(cx - 13, cy - 4 + bounce - eye_h, cx - 7, cy - 4 + bounce + eye_h, fill="#18181B", outline="")
            self.canvas.create_oval(cx + 7, cy - 4 + bounce - eye_h, cx + 13, cy - 4 + bounce + eye_h, fill="#18181B", outline="")

            # Expresion de calma o actividad
            if self.state == "active":
                self.canvas.create_arc(cx - 5, cy + 2 + bounce, cx + 5, cy + 8 + bounce, start=0, extent=-180, fill="#27272D", outline="")
            else:
                # Pequena sonrisita serena
                self.canvas.create_line(cx - 3, cy + 4 + bounce, cx + 3, cy + 4 + bounce, fill="#71717A", width=1.5)

        # Rubor sutil (tonos grises calidos)
        self.canvas.create_oval(cx - 18, cy + bounce, cx - 12, cy + 4 + bounce, fill="#E4E4E7", outline="")
        self.canvas.create_oval(cx + 12, cy + bounce, cx + 18, cy + 4 + bounce, fill="#E4E4E7", outline="")


# ── Aplicacion de Escritorio Minimalista ─────────────────────────────────
class VitamonMinimalApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Configuracion de Ventana
        self.title("Vitamon")
        self.geometry("300x520+1240+140")
        self.minsize(280, 360)
        self.attributes("-topmost", True)
        self.configure(fg_color=PALETTE["bg_main"])

        # Arrastre de ventana sin bordes
        self.is_pinned = True
        self.is_compact = False
        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.do_drag)

        # Colas de comunicacion
        self.data_queue = queue.Queue()
        self.cmd_queue = queue.Queue()

        # Metricas
        self.metrics = {
            "hr": 76,
            "spo2": 98,
            "steps": 2934,
            "calories": 210,
            "battery": 100,
            "status": "Conectando"
        }

        # Construir Interfaz Minimalista
        self.setup_ui()

        # Iniciar Mascota
        self.creature = MinimalCreature(self.canvas_creature)

        # Iniciar Hilo BLE
        self.ble_thread = BLEBridgeThread(self.data_queue, self.cmd_queue)
        self.ble_thread.start()

        # Ciclos de Actualizacion
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
        self.btn_pin.configure(text="Fijado" if self.is_pinned else "Libre",
                               fg_color=PALETTE["accent_subtle"] if self.is_pinned else "transparent")

    def toggle_compact(self):
        self.is_compact = not self.is_compact
        if self.is_compact:
            self.geometry("300x240")
            self.frame_metrics.pack_forget()
            self.frame_analytics.pack_forget()
            self.frame_controls.pack_forget()
            self.btn_compact.configure(text="Extendido")
        else:
            self.geometry("300x520")
            self.frame_metrics.pack(fill="x", padx=14, pady=4)
            self.frame_analytics.pack(fill="x", padx=14, pady=4)
            self.frame_controls.pack(fill="x", padx=14, pady=4)
            self.btn_compact.configure(text="Compacto")

    def setup_ui(self):
        # 1. Cabecera Minimalista
        self.frame_header = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_header.pack(fill="x", padx=14, pady=(12, 2))

        self.lbl_brand = ctk.CTkLabel(self.frame_header, text="VITAMON", font=("Segoe UI", 11, "bold"),
                                     text_color=PALETTE["text_secondary"])
        self.lbl_brand.pack(side="left")

        self.btn_compact = ctk.CTkButton(self.frame_header, text="Compacto", width=62, height=22,
                                         fg_color="transparent", border_width=1, border_color=PALETTE["card_border"],
                                         text_color=PALETTE["text_muted"], font=("Segoe UI", 9),
                                         command=self.toggle_compact)
        self.btn_compact.pack(side="right", padx=(4, 0))

        self.btn_pin = ctk.CTkButton(self.frame_header, text="Fijado", width=48, height=22,
                                     fg_color=PALETTE["accent_subtle"], border_width=1, border_color=PALETTE["card_border"],
                                     text_color=PALETTE["text_primary"], font=("Segoe UI", 9),
                                     command=self.toggle_pin)
        self.btn_pin.pack(side="right")

        # 2. Tarjeta Mascota
        self.card_mascot = ctk.CTkFrame(self, fg_color=PALETTE["card_bg"], corner_radius=12,
                                        border_width=1, border_color=PALETTE["card_border"])
        self.card_mascot.pack(fill="x", padx=14, pady=6)

        self.canvas_creature = tk.Canvas(self.card_mascot, width=260, height=130,
                                         bg=PALETTE["card_bg"], highlightthickness=0)
        self.canvas_creature.pack(pady=(6, 0))

        self.lbl_level = ctk.CTkLabel(self.card_mascot, text="Brote  •  Nivel 1",
                                     font=("Segoe UI", 11), text_color=PALETTE["text_secondary"])
        self.lbl_level.pack(pady=(0, 2))

        # Barra de progreso sobria
        self.progress_xp = ctk.CTkProgressBar(self.card_mascot, height=4,
                                              progress_color=PALETTE["accent"],
                                              fg_color=PALETTE["divider"])
        self.progress_xp.pack(fill="x", padx=24, pady=(2, 10))
        self.progress_xp.set(0.4)

        # 3. Metricas Principales (2 Columnas Limpias)
        self.frame_metrics = ctk.CTkFrame(self, fg_color=PALETTE["card_bg"], corner_radius=12,
                                         border_width=1, border_color=PALETTE["card_border"])
        self.frame_metrics.pack(fill="x", padx=14, pady=4)

        self.frame_metrics.columnconfigure(0, weight=1)
        self.frame_metrics.columnconfigure(1, weight=1)

        # Columna Izquierda: Pulso
        self.lbl_hr_tag = ctk.CTkLabel(self.frame_metrics, text="PULSO", font=("Segoe UI", 9, "bold"), text_color=PALETTE["text_muted"])
        self.lbl_hr_tag.grid(row=0, column=0, padx=14, pady=(8, 0), sticky="w")

        self.lbl_hr_val = ctk.CTkLabel(self.frame_metrics, text="76 bpm", font=("Segoe UI", 16, "bold"), text_color=PALETTE["text_primary"])
        self.lbl_hr_val.grid(row=1, column=0, padx=14, pady=(0, 8), sticky="w")

        # Columna Derecha: Pasos
        self.lbl_steps_tag = ctk.CTkLabel(self.frame_metrics, text="PASOS", font=("Segoe UI", 9, "bold"), text_color=PALETTE["text_muted"])
        self.lbl_steps_tag.grid(row=0, column=1, padx=14, pady=(8, 0), sticky="w")

        self.lbl_steps_val = ctk.CTkLabel(self.frame_metrics, text="2,934", font=("Segoe UI", 16, "bold"), text_color=PALETTE["text_primary"])
        self.lbl_steps_val.grid(row=1, column=1, padx=14, pady=(0, 8), sticky="w")

        # Fila 2: SpO2 y Bateria
        self.lbl_spo2_tag = ctk.CTkLabel(self.frame_metrics, text="OXIGENO", font=("Segoe UI", 9, "bold"), text_color=PALETTE["text_muted"])
        self.lbl_spo2_tag.grid(row=2, column=0, padx=14, pady=(4, 0), sticky="w")

        self.lbl_spo2_val = ctk.CTkLabel(self.frame_metrics, text="98 %", font=("Segoe UI", 14), text_color=PALETTE["text_primary"])
        self.lbl_spo2_val.grid(row=3, column=0, padx=14, pady=(0, 10), sticky="w")

        self.lbl_bat_tag = ctk.CTkLabel(self.frame_metrics, text="BATERIA", font=("Segoe UI", 9, "bold"), text_color=PALETTE["text_muted"])
        self.lbl_bat_tag.grid(row=2, column=1, padx=14, pady=(4, 0), sticky="w")

        self.lbl_bat_val = ctk.CTkLabel(self.frame_metrics, text="100 %", font=("Segoe UI", 14), text_color=PALETTE["text_primary"])
        self.lbl_bat_val.grid(row=3, column=1, padx=14, pady=(0, 10), sticky="w")

        # 4. Analitica y Proyecciones Sobrias
        self.frame_analytics = ctk.CTkFrame(self, fg_color=PALETTE["card_bg"], corner_radius=12,
                                           border_width=1, border_color=PALETTE["card_border"])
        self.frame_analytics.pack(fill="x", padx=14, pady=4)

        self.lbl_pred_title = ctk.CTkLabel(self.frame_analytics, text="ESTADO Y ESTIMACIONES", font=("Segoe UI", 9, "bold"), text_color=PALETTE["text_muted"])
        self.lbl_pred_title.pack(anchor="w", padx=14, pady=(8, 2))

        self.lbl_pred_steps = ctk.CTkLabel(self.frame_analytics, text="Proyeccion 23:59: ~4,800 pasos",
                                          font=("Segoe UI", 10), text_color=PALETTE["text_secondary"])
        self.lbl_pred_steps.pack(anchor="w", padx=14, pady=1)

        self.lbl_stress = ctk.CTkLabel(self.frame_analytics, text="Indice de tension: Moderado bajo",
                                      font=("Segoe UI", 10), text_color=PALETTE["text_secondary"])
        self.lbl_stress.pack(anchor="w", padx=14, pady=(1, 8))

        # 5. Botones de Control Limpios
        self.frame_controls = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_controls.pack(fill="x", padx=14, pady=6)

        self.btn_mute = ctk.CTkButton(self.frame_controls, text="Silenciar avisos", height=26,
                                      fg_color=PALETTE["card_bg"], border_width=1, border_color=PALETTE["card_border"],
                                      text_color=PALETTE["text_secondary"], font=("Segoe UI", 9),
                                      command=self.action_silence)
        self.btn_mute.pack(side="left", fill="x", expand=True, padx=(0, 2))

        self.btn_sync = ctk.CTkButton(self.frame_controls, text="Sincronizar", height=26,
                                      fg_color=PALETTE["card_bg"], border_width=1, border_color=PALETTE["card_border"],
                                      text_color=PALETTE["text_secondary"], font=("Segoe UI", 9),
                                      command=self.action_sync)
        self.btn_sync.pack(side="left", fill="x", expand=True, padx=2)

        self.btn_vibe = ctk.CTkButton(self.frame_controls, text="Vibracion", height=26,
                                      fg_color=PALETTE["card_bg"], border_width=1, border_color=PALETTE["card_border"],
                                      text_color=PALETTE["text_secondary"], font=("Segoe UI", 9),
                                      command=self.action_vibrate)
        self.btn_vibe.pack(side="left", fill="x", expand=True, padx=(2, 0))

        # 6. Estado Inferior
        self.lbl_status = ctk.CTkLabel(self, text="Conectado a Air5", font=("Segoe UI", 9),
                                       text_color=PALETTE["text_muted"])
        self.lbl_status.pack(side="bottom", pady=(2, 6))

    def action_silence(self):
        self.cmd_queue.put({"action": "silence_sedentary"})
        messagebox.showinfo("Avisos", "Aviso de inactividad silenciado.")

    def action_sync(self):
        self.cmd_queue.put({"action": "sync_time"})
        messagebox.showinfo("Hora", "Hora sincronizada con el sistema.")

    def action_vibrate(self):
        self.cmd_queue.put({"action": "vibrate"})

    def process_ble_queue_loop(self):
        try:
            while not self.data_queue.empty():
                msg = self.data_queue.get_nowait()
                m_type = msg.get("type")

                if m_type == "status":
                    text = msg.get("msg")
                    self.lbl_status.configure(text=f"Air5 • {text}")

                elif m_type == "live_hr":
                    bpm = msg.get("value")
                    self.metrics["hr"] = bpm
                    self.lbl_hr_val.configure(text=f"{bpm} bpm")

                elif m_type == "live_spo2":
                    val = msg.get("value")
                    self.metrics["spo2"] = val
                    self.lbl_spo2_val.configure(text=f"{val} %")

                elif m_type == "daily_activity":
                    steps = msg.get("steps")
                    self.metrics["steps"] = steps
                    self.lbl_steps_val.configure(text=f"{steps:,}")

                elif m_type == "battery":
                    pct = msg.get("value")
                    self.metrics["battery"] = pct
                    self.lbl_bat_val.configure(text=f"{pct} %")

        except queue.Empty:
            pass

        self.after(100, self.process_ble_queue_loop)

    def update_animation_loop(self):
        self.creature.update_stats(self.metrics["steps"], self.metrics["hr"])
        self.creature.render()

        stage_name = self.creature.get_stage_name()
        self.lbl_level.configure(text=f"{stage_name}  •  Nivel {self.creature.level}")

        cur_progress = (self.metrics["steps"] % 1000) / 1000.0
        self.progress_xp.set(cur_progress)

        if self.creature.frame % 25 == 0:
            now = datetime.now()
            hours_passed = max(1, now.hour + now.minute / 60.0)
            hours_left = max(0, 24 - hours_passed)
            step_rate = self.metrics["steps"] / hours_passed
            pred = int(self.metrics["steps"] + (step_rate * hours_left))
            self.lbl_pred_steps.configure(text=f"Proyeccion 23:59: ~{pred:,} pasos")

        self.after(50, self.update_animation_loop)


if __name__ == "__main__":
    app = VitamonMinimalApp()
    app.mainloop()
