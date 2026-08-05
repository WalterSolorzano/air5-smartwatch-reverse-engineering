# 🛰️ Air5 Smartwatch Reverse Engineering & Vitamon Companion 🐾

[![BLE Protocol Reverse Engineered](https://img.shields.io/badge/BLE-Reverse_Engineered-success?style=for-the-badge&logo=bluetooth)](protocol_map.md)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python)](https://www.python.org/)
[![UI CustomTkinter](https://img.shields.io/badge/UI-CustomTkinter_Glassmorphism-purple?style=for-the-badge)](https://github.com/TomSchimansky/CustomTkinter)
[![License MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

An exhaustive, byte-level reverse-engineered Bluetooth Low Energy (BLE) protocol implementation for the **Air5 Smartwatch (ID-1DBC)** and generic **GloryFit / Moyoung / MOY-xxx** BLE devices, along with **Vitamon** — a floating desktop cyberpunk health companion & Tamagotchi widget that lives on your PC screen and reacts to your real-time physical metrics.

---

## 📖 Table of Contents
- [✨ Key Features](#-key-features)
- [🔬 The Reverse Engineering Story & Windows BLE Architecture](#-the-reverse-engineering-story--windows-ble-architecture)
- [🗺️ Complete GATT Architecture & Handles](#️-complete-gatt-architecture--handles)
- [⚡ Handshake Sequence & Channel Initialization](#-handshake-sequence--channel-initialization)
- [📦 Complete Command & Packet Dictionary](#-complete-command--packet-dictionary)
  - [1. Real-time PPG Heart Rate Stream (`E5`)](#1-real-time-ppg-heart-rate-stream-e5)
  - [2. Daily Aggregated Activity (`26 01`)](#2-daily-aggregated-activity-26-01)
  - [3. Battery Telemetry (`A2`)](#3-battery-telemetry-a2)
  - [4. Sedentary Spam Bug & Suppression (`D1` / `D7`)](#4-sedentary-spam-bug--suppression-d1--d7)
  - [5. 24-Hour Heart Rate History (`F7`)](#5-24-hour-heart-rate-history-f7)
  - [6. SpO2 Blood Oxygen History (`34 FA`)](#6-spo2-blood-oxygen-history-34-fa)
  - [7. Sleep Stages & Sleep Debt Analysis (`32` / `CB`)](#7-sleep-stages--sleep-debt-analysis-32--cb)
  - [8. Push Notifications to Watch Screen (`12`)](#8-push-notifications-to-watch-screen-12)
  - [9. Custom Watchfaces & OTA Background Transfer (`6001` / `6002`)](#9-custom-watchfaces--ota-background-transfer-6001--6002)
- [🐾 Vitamon: Cyberpunk Health Tamagotchi Desktop Widget](#-vitamon-cyberpunk-health-tamagotchi-desktop-widget)
- [🚀 Quickstart & Installation](#-quickstart--installation)
- [🛠️ Repository Structure](#️-repository-structure)

---

## ✨ Key Features

1. **Full Protocol Specification**: Byte-level documentation of all opcodes, packet formats, checksums, and endianness.
2. **Native Windows WinRT BLE Bridge**: Reliable communication bypassing standard Python `bleak` limitations with generic Barrot / Realtek USB dongles.
3. **Anti-Spam Sedentary Fix**: Neutralizes the infamous non-stop *"Has estado sentado demasiado tiempo"* vibration loop that drains battery.
4. **Interactive Vitamon Tamagotchi Widget**:
   - Animated procedural pixel creature with dynamic emotional states (Happy, Hyper, Sleepy, Meditating, Cheering).
   - Real-time RPG evolution system (Egg ➔ Sprite ➔ Mecha Fox ➔ Cosmic Dragon) driven by your real step count.
   - Predictive Health Engine: Step projection at 23:59, Autonomic Stress Index (HRV proxy), and Body Battery score.
5. **Data Exporter**: Synchronizes 7-day historical telemetry and live streams to clean JSON and CSV files.

---

## 🔬 The Reverse Engineering Story & Windows BLE Architecture

### 1. Packet Sniffing & BTSnoop Extraction
The protocol was extracted using Android developer HCI Bluetooth snoop logs (`btsnoop_hci.log`) captured during active sync with the official **GloryFit** application. Using custom Python parsers (`decode_all.py`, `investigate.py`), we decoded the ATT notification tables across handles `0x0011` through `0x0025`.

### 2. The Windows Barrot / Realtek Dongle Dilemma
When pairing the Air5 to Windows 10/11, Windows pairs the device as a Bluetooth Classic headset (HFP/A2DP for microphone and calls). Standard multiplatform libraries like `bleak` often fail to discover the GATT services when standard generic dongles (e.g. Barrot BR8651) hold the ACL link.
**Solution**: Direct integration with Windows Runtime APIs (`winrt.windows.devices.bluetooth.BluetoothLEDevice` and `GattSession`). By calling `get_gatt_services_async()`, Windows is forced to establish the BLE GATT connection instantly.

---

## 🗺️ Complete GATT Architecture & Handles

The Air5 exposes 8 GATT services. The communication takes place over two primary multiplexed bidirectional channels and one OTA channel:

| Service UUID | TX Handle (PC ➔ Watch) | RX Handle (Watch ➔ PC) | Characteristic UUID | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `000055ff-0000-1000-8000-00805f9b34fb` | **`0x0011`** (Write) | **`0x0013`** (Notify `0x0014`) | `000033f1` / `000033f2` | **Channel 1**: Real-time heart rate, steps, battery, time sync, alarms, sedentary config |
| `000056ff-0000-1000-8000-00805f9b34fb` | **`0x0017`** (Write) | **`0x0019`** (Notify `0x001a`) | `000034f1` / `000034f2` | **Channel 2**: SpO2 blood oxygen, detailed sleep stages, hardware serial & firmware info |
| `000060ff-0000-1000-8000-00805f9b34fb` | **`0x001D`** (Write) | **`0x001F`** (Notify `0x0020`) | `00006001` / `00006002` | **Channel 3 (OTA)**: Binary chunked Watchface & Background upload |
| `0000180f-0000-1000-8000-00805f9b34fb` | — | **`0x0029`** (Read/Notify) | `00002a19` | Standard GATT Battery Service |

---

## ⚡ Handshake Sequence & Channel Initialization

To start receiving telemetry, both notification descriptors must be enabled (`CCCD = 0x0001`), followed by the initialization handshake:

```python
# 1. Enable Notifications on Handles 0x0013 and 0x0019
await notify_ch1.write_client_characteristic_configuration_descriptor_async(GattClientCharacteristicConfigurationDescriptorValue.NOTIFY)
await notify_ch2.write_client_characteristic_configuration_descriptor_async(GattClientCharacteristicConfigurationDescriptorValue.NOTIFY)

# 2. Handshake Channel 1 (Write to 0x0011)
await write_ch1("0808442a01243943756ffffed921005f784be1dc")

# 3. Handshake Channel 2 (Write to 0x0017)
await write_ch2("00f4000000000000000000000000000000000402")

# 4. Sync RTC Time (Write to 0x0011): a3 [YYYY_2B] [MM] [DD] [HH] [mm] [SS]
# Example: 2026-08-04 19:10:46 -> a3 07 ea 08 04 13 0a 2e
```

---

## 📦 Complete Command & Packet Dictionary

### 1. Real-time PPG Heart Rate Stream (`E5`)
Once initialized, the watch continuously broadcasts real-time optical PPG sensor readings:
- **Direction**: Watch ➔ PC (Handle `0x0014`)
- **Format**: `E5 11 00 [BPM]`
- **Example**: `e5 11 00 68` ➔ `0x68` = **104 BPM**

### 2. Daily Aggregated Activity (`26 01`)
- **Request**: PC ➔ Watch `26 01`
- **Response**: `26 01 [Mask_4B] [Steps_2B_LE] [Calories_2B_LE] [Distance_2B_LE] [ActiveMinutes_1B]`
- **Example**: `26 01 ff ff ff ff 01 40 01 7c 01 00 10 ...`
  - Steps: `0x0140` = 320 steps
  - Calories: `0x017C` = 380 kcal
  - Active time: `16` minutes

### 3. Battery Telemetry (`A2`)
- **Request**: PC ➔ Watch `A2`
- **Response**: `A2 [Percentage_1B]`
- **Example**: `a2 64` = 100%, `a2 10` = 16%

### 4. Sedentary Spam Bug & Suppression (`D1` / `D7`)
The watch firmware defaults to an aggressive sedentary alert (`d1 0a 00` = every 10 min, 0 step threshold), causing infinite vibrations.
- **Fix / Silence Command**:
  1. `D1 FF 64`: Sets interval to 255 minutes with a 100 step threshold.
  2. `D7 16 00 17 00 00 00`: Restricts alert active window to 22:00–23:00 only.

### 5. 24-Hour Heart Rate History (`F7`)
- **Request**: `F7 FA [YYYY_2B] [MM] [DD] [HH] [mm]`
- **Response Frames**: `F7 [YYYY_2B] [MM] [DD] [HourPage_1B] [12 x BPM_1B]`
  - Each page represents 1 hour.
  - 12 measurements per hour (1 every 5 minutes).

### 6. SpO2 Blood Oxygen History (`34 FA`)
- **Request**: `34 FA` (on Channel 2 / Handle `0x0017`)
- **Response**: `34 FA [YYYY_2B] [MM] [DD] [Hour_1B] [14 Bytes Raw PPG] [SpO2_Percentage_1B]`
- **Example**: `34 fa 07 ea 08 04 12 ... 62` ➔ `0x62` = **98% SpO2**

### 7. Sleep Stages & Sleep Debt Analysis (`32` / `CB`)
- **Response (Handle `0x001a`)**: `32 [HH] [mm] [Stage_1B] [Duration_2B_LE]`
  - Stage `0x01`: Light Sleep
  - Stage `0x02`: Deep Sleep
  - Stage `0x03`: REM Sleep
  - Stage `0x04`: Awake

### 8. Push Notifications to Watch Screen (`12`)
Push custom PC notifications (Discord, WhatsApp, Email, System alerts) to the smartwatch display:
- **Format**: `12 [AppID_1B] [TitleLength_1B] [Title_ASCII] [Message_ASCII]`
- **AppIDs**: `01` = Phone Call, `02` = SMS, `03` = WeChat, `08` = WhatsApp, `09` = Discord/Twitter.

### 9. Custom Watchfaces & OTA Background Transfer (`6001` / `6002`)
Custom dials / watchfaces are uploaded through Service `000060ff-...`:
1. OTA Handshake on `0x001D` with image byte size and header.
2. 240x280 RGB565 binary raw image streaming in 20-to-244 byte chunks indexed by sequence IDs.
3. Watch confirms every block via `0x6002 [Seq_2B] 01` on handle `0x001F`.

---

## 🐾 Vitamon: Cyberpunk Health Tamagotchi Desktop Widget

`vitamon_app.py` is a native Windows desktop floating companion built with CustomTkinter:

- **Animated Canvas Creature**: Dynamic moods responding in real time to your biological metrics:
  - 💖 **Calm / Zen**: Heart rate within 60–85 BPM.
  - 🔥 **Hyper Mode**: Heart rate >95 BPM or high step cadence (glowing flame aura!).
  - 💤 **Sleepy**: Inactivity or low heart rate (floating zZz bubbles).
  - 🎉 **Cheering**: Triggers when reaching step milestones.
- **Evolution & RPG Levels**: Gains XP with real steps and active workout minutes:
  - Level 1: *Huevito Cyber (Cyber Egg)*
  - Level 2–4: *Aero Sprite (Floating Spirit)*
  - Level 5–9: *Mecha Zorro (Cyber Fox)*
  - Level 10+: *Cyber Dragón Cósmico (Cosmic Dragon)*
- **Smart Predictive Engine**:
  - **End-of-day Step Projection**: Real-time hourly velocity modeling to predict step counts at 23:59.
  - **Autonomic Stress Index (0–100)**: Derived from live pulse variability and cardiac zones.
  - **Body Battery (0–100%)**: Dynamic personal energy gauge.
- **Desktop Features**:
  - 📌 **Always on Top** toggle.
  - 🗕 **Mini Mode**: Collapse into an ultra-compact pixel HUD.
  - 🔕 **1-Click Sedentary Spam Mute**.
  - ⏰ **1-Click RTC Time Sync**.
  - 📳 **Find Watch (Vibrate Motor)**.

---

## 🚀 Quickstart & Installation

### 1. Prerequisites
- Windows 10/11 PC with Bluetooth.
- Python 3.10 or higher.
- Air5 (or compatible GloryFit/Moyoung) smartwatch paired in Windows Settings.

### 2. Install Dependencies
```bash
git clone https://github.com/WalterSolorzano/air5-smartwatch-reverse-engineering.git
cd air5-smartwatch-reverse-engineering
pip install -r requirements.txt
```

### 3. Run the Vitamon Tamagotchi Companion
```bash
python vitamon_app.py
```

### 4. Run the Full Sync CLI (Exports JSON & CSV)
```bash
python air5_sync.py
```

---

## 🛠️ Repository Structure

```
├── README.md               # Main documentation & complete protocol guide
├── protocol_map.md         # Exhaustive byte-by-byte protocol mapping reference
├── vitamon_app.py          # Modern Cyberpunk Tamagotchi Floating Desktop Widget
├── air5_sync.py            # CLI synchronizer (extracts 7-day history to JSON/CSV)
├── talker_winrt.py         # Low-level WinRT interactive BLE terminal communicator
├── decode_all.py           # BTSnoop / HCI log decoder and stream parser
├── requirements.txt        # Python package dependencies
├── .gitignore              # Git ignore configuration
└── LICENSE                 # MIT License
```

---

## 📜 License
This project is open-source and released under the [MIT License](LICENSE).
Developed for educational, research, and interoperability purposes.
