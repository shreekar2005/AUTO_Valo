# NXP HID AI Proxy

This project captures a specific Region of Interest (ROI) on the screen, runs it through a YOLOv8 AI object detection model on an NVIDIA GPU, and sends physical hardware actuation commands to an NXP i.MX91 board via UDP.

The NXP board acts as a physical USB Gadget (Hardware Proxy), meaning the target computer sees standard USB Mouse/Keyboard inputs, not software-simulated inputs.

## Architecture
1. **Vision Thread (Brain):** Runs YOLOv8 inference to detect targets and apply Exponential Moving Average (EMA) filtering to prevent bounding-box jitter.
2. **Movement Thread (Hands):** Runs a high-speed (~200Hz) Proportional Controller loop to stream smooth micro-movements to the NXP board.

## Hotkey Controls
Ensure the terminal running the Python script has administrator privileges so the `keyboard` module can listen globally.

* **`[UP ARROW]`** : Toggle Aimbot (Sends 'x' and 'y' mouse coordinates).
* **`[DOWN ARROW]`** : Toggle Triggerbot (Sends 'b': 1 when crosshair is on target).
* **`[Q]`** : Quit the program safely.

## UDP Command Protocol (JSON Format)
The Python server running on the NXP board expects JSON payloads over UDP port `5005`.

### 1. Mouse Commands
**Format:** `{"t": "m", "x": <int>, "y": <int>, "b": <int>}`

* `"t"` : Type. `"m"` indicates a Mouse action.
* `"x"` : X-axis relative movement. Valid range: `-127` to `+127`. (Negative = Left)
* `"y"` : Y-axis relative movement. Valid range: `-127` to `+127`. (Negative = Up)
* `"b"` : Button state bitmask. 
  * `0` = Release all buttons
  * `1` = Left Click
  * `2` = Right Click
  * `4` = Middle Click

### 2. Keyboard Commands (Standard HID spec for future use)
**Format:** `{"t": "k", "m": <int>, "k": [<int>, <int>, ...]}`

* `"t"` : Type. `"k"` indicates a Keyboard action.
* `"m"` : Modifier keys bitmask (e.g., `2`=Left Shift, `1`=Left Ctrl, `0`=None).
* `"k"` : List of standard USB HID Keycodes (e.g., `4`='A', `44`='Space').

## Tuning and Configuration
Inside `yolo_aim.py`, you can adjust the following parameters to suit your screen and sensitivity:

* **`Kp = 0.4`**: Proportional aggression. Increase if aiming feels too slow/lags behind. Decrease if it oscillates/wiggles.
* **`MAX_STEP = 5`**: Hard speed limit for mouse movement per tick to maintain smoothness.
* **`DEADZONE = 3`**: Radius in pixels. If the crosshair is within this radius of the target, movement stops to prevent micro-jitters.
* **`FILTER_ALPHA = 0.25`**: Trust factor for new AI frames vs history. Lower = smoother but slight tracking delay. Higher = snappier but jittery.