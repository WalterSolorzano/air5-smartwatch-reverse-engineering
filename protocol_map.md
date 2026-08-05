# 🛰️ Air5 (ID-1DBC) Smartwatch — Bluetooth BLE Protocol Reference

Este documento detalla exhaustivamente la ingeniería inversa del protocolo de comunicación BLE (Bluetooth Low Energy) entre la **PC / Teléfono** y el **Smartwatch Air5 (ID-1DBC)** con chipset **MOY-xxx / GloryFit**.

---

## 1. Arquitectura de Conexión y Servicios GATT

- **Nombre BLE**: `Air5(ID-1DBC)`
- **Dirección MAC BLE**: `81:0A:B7:00:1D:BC`
- **Nota Windows**: Al emparejarse en Windows, el dispositivo conecta audio clásico (HFP/A2DP para llamadas/micrófono) y expone simultáneamente el servidor GATT BLE. En Windows, la API nativa recomendada es **WinRT (`winrt.windows.devices.bluetooth`)**.

### Servicios y Características GATT Descubiertos

| Servicio UUID | Handle Ch | Característica UUID | Propiedades | Función |
| :--- | :--- | :--- | :--- | :--- |
| `000055ff-...-00805f9b34fb` | `0x0011` | `000033f1-...` | WRITE (Write Without Resp) | **Canal 1 TX**: Comandos primarios, hora, sedentarismo, actividad, alarmas |
| `000055ff-...-00805f9b34fb` | `0x0013` (val `0x0014`) | `000033f2-...` | NOTIFY | **Canal 1 RX**: FC en vivo, batería, pasos hoy, historial FC 24h, respuestas |
| `000056ff-...-00805f9b34fb` | `0x0017` | `000034f1-...` | WRITE | **Canal 2 TX**: Handshake secundario, consultas SpO2 e info HW |
| `000056ff-...-00805f9b34fb` | `0x0019` (val `0x001a`) | `000034f2-...` | NOTIFY | **Canal 2 RX**: SpO2 histórico, etapas de sueño (0x32/0xCB), Device ID (0x38) |
| `000060ff-...-00805f9b34fb` | `0x001D` | `00006001-...` | WRITE | **Canal OTA / Watchface TX**: Envío de imágenes binarias y fondos de pantalla |
| `000060ff-...-00805f9b34fb` | `0x001F` (val `0x0020`) | `00006002-...` | NOTIFY | **Canal OTA / Watchface RX**: ACKs de bloques de transferencia de fondo |
| `0000180f-...-00805f9b34fb` | `0x0029` | `00002a19-...` | READ / NOTIFY | **GATT Standard Battery Service**: Lectura de nivel de batería |
| `0000d0ff-3c17-...-14fe2e4da212` | `0x002D`..`0x003F`| `0000ffd1`.. | VENDOR | Parámetros del fabricante (Moyoung / Goodix) |

---

## 2. Secuencia de Inicialización y Handshake

Para iniciar la comunicación bidireccional, se debe suscribir a `NOTIFY` en Canal 1 (`0x0013`) y Canal 2 (`0x0019`), y enviar la secuencia de Handshake:

```
PC -> Watch (0x0011): 08 08 44 2a 01 24 39 43 75 6f ff fe d9 21 00 5f 78 4b e1 dc
PC -> Watch (0x0017): 00 f4 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 04 02
```

---

## 3. Diccionario Completo de Comandos (PC ↔ Reloj)

### 3.1. Control y Configuración del Sistema

| Opcode | Dirección | Estructura / Payload (Hex) | Descripción |
| :--- | :--- | :--- | :--- |
| `A3` | PC → Reloj | `a3 [YYYY_2b] [MM] [DD] [HH] [mm] [SS]` | **Sincronización de Hora y Fecha**. Ejemplo: `a3 07 ea 08 04 13 0a 2e` = 2026-08-04 19:10:46 |
| `A1` | PC → Reloj | `a1` | **Solicitar Número de Serie**. Reloj responde con `a1 [ASCII_SERIAL]` |
| `A2` | PC → Reloj | `a2` | **Solicitar Batería**. Reloj responde: `a2 [PCT]` (ej: `a2 10` = 16%) |
| `BB` | PC → Reloj | `bb` | **Solicitar Estado General**. Reloj responde: `bb 01` (OK) |
| `D1` | PC → Reloj | `d1 [Interval_Min] [Threshold_Steps]` | **Configurar Alerta de Sedentarismo**.<br>• `d1 0a 00` = Cada 10 min, umbral 0 pasos (SPAM AGRESIVO).<br>• `d1 ff 64` = Cada 255 min, umbral 100 pasos (**SILENCIADO / ANTI-SPAM**). |
| `D7` | PC → Reloj | `d7 [Start_H] [Start_M] [End_H] [End_M] 00 00` | **Ventana Horaria de Sedentarismo**. Ejemplo: `d7 16 00 17 00 00 00` = Solo de 22:00 a 23:00 hs. |
| `D2` | PC → Reloj | `d2 01` / `d2 00` | **Buscar Reloj / Vibración**. `01` activa vibrador, `00` apaga. |
| `12` | PC → Reloj | `12 [Tipo] [Len] [Texto ASCII/UTF8]` | **Push Notification a Pantalla**. Tipo: `01`=Llamada, `02`=SMS, `03`=WhatsApp/Discord. Muestra remitente y mensaje en pantalla. |

---

### 3.2. Telemetría en Vivo (Streaming)

| Opcode | Dirección | Estructura / Payload | Interpretación |
| :--- | :--- | :--- | :--- |
| `E5` | Reloj → PC | `e5 11 00 [BPM]` | **Frecuencia Cardíaca en Tiempo Real**. Ejemplo: `e5 11 00 68` = 104 BPM, `e5 11 00 64` = 100 BPM. |
| `B1` | Reloj → PC | `b1 [YYYY_2b] [MM] [DD] [HH] [MM] ... [Steps_2b]` | **Incremento de Pasos por Bloque Horario Actual**. |
| `26` | Reloj ↔ PC | Req: `26 01`<br>Resp: `26 01 [Flags_4b] [Pasos_2b] [Cal_2b] [Dist_2b] [MinActivos_1b] ...` | **Resumen del Día de Actividad** (Pasos totales, Kcal quemadas, Distancia metros, Tiempo activo). |

---

### 3.3. Datos Históricos y Sueño

| Opcode | Dirección | Estructura / Payload | Interpretación |
| :--- | :--- | :--- | :--- |
| `F7` | Reloj ↔ PC | Req: `f7 fa [YYYY_2b] [MM] [DD] [HH] [mm]`<br>Resp: `f7 [YYYY_2b] [MM] [DD] [Page_1b] [12 x BPMs]` | **Historial de Frecuencia Cardíaca (24 horas)**. Cada página representa 1 hora con 12 mediciones (cada 5 min). |
| `34` | Reloj ↔ PC | Req: `34 fa`<br>Resp: `34 fa [YYYY_2b] [MM] [DD] [HH] ... [SpO2_1b]` | **Historial de Saturación de Oxígeno (SpO2 %)**. Rango válido 70-100%. |
| `32` / `CB` | Reloj ↔ PC | `32 [HH] [mm] [Stage] [Dur_2b]`<br>`cb [Page] [Count] [Records...]` | **Historial de Etapas de Sueño**.<br>• `Stage 01`: Sueño Ligero (Light Sleep)<br>• `Stage 02`: Sueño Profundo (Deep Sleep)<br>• `Stage 03`: Fase REM<br>• `Stage 04`: Despierto (Awake). |
| `B2` | Reloj ↔ PC | Req: `b2 fa`<br>Resp: `b2 [YYYY_2b] [MM] [DD] [Page_2b] [Bytes...]` | **Historial de Pasos por Hora**. |
| `38` | Reloj ↔ PC | `38 01 [Name_14b] [MAC_6b] [FW_Ver_3b]` | **Información de Hardware y Firmware**. Retorna `"Air5(ID-1DBC)"`, dirección MAC y versión de firmware. |
| `46` | Reloj ↔ PC | `46 fa 05 [Slot_0..4]` | **Gestión de 5 Ranuras de Alarma / Despertador**. |

---

## 4. Protocolo de Fondos de Pantalla y Watchfaces Personalizadas

El servicio `000060ff-...` con características `0x001D` (Write) y `0x001F` (Notify) gestiona la carga de diales (Watchfaces):
1. **Handshake OTA**: Se envía cabecera con tamaño de imagen total (resolución típica 240x280 o 240x240 en formato RGB565 binario sin compresión o RLE).
2. **Chunking**: Se transmiten paquetes de 20 a 244 bytes indexados con número de secuencia `[Seq_2b] [Payload...]`.
3. **ACK**: El reloj responde en `0x001F` con `0x6002 [Seq_2b] 01` para confirmar cada bloque recibido.

---

## 5. Prevención y Silenciamiento del Spam de Sedentarismo

El reloj cuenta con una función de aviso de inactividad que, si se configura con intervalo corto (`d1 0a 00`), envía alertas y vibraciones ininterrumpidas cada 10 minutos.

**Solución definitiva en PC**:
1. Enviar `d1 ff 64` (Establece intervalo a 255 minutos y umbral de 100 pasos).
2. Enviar `d7 16 00 17 00 00 00` (Restringe la ventana activa únicamente de 22:00 a 23:00 hs).
Esto neutraliza por completo cualquier spam durante el uso diario.
