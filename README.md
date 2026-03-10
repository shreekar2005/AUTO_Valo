Here is a comprehensive `README.md` file you can use for your project. It breaks down the setup process, explains the difference between your two main scripts, and provides a detailed guide on how to tweak the aiming variables to perfection.

---

# YOLOv5 NXP Hardware Aim Assist

This project is a computer vision-based aim assist and auto-shot script. It uses YOLOv5 to detect targets on-screen and sends physical mouse movement commands to an external NXP hardware board over UDP to bypass standard software-level mouse hooks.

**⚠️ DISCLAIMER:** This project is for educational purposes and offline aim-training environments. Modern kernel-level anti-cheats (like Riot Vanguard) actively scan for top-most transparent overlays, high-frequency screen captures (DXGI/GDI), and unauthorized USB hardware descriptors. Using this in live multiplayer environments may result in a Hardware ID (HWID) ban.

## 📂 Project Structure

* **`main.py`**: The "Stealth" version. Runs purely in the background with a minimal Tkinter text overlay. Best for performance and actual use.
* **`main_debug.py`**: The Developer version. Opens an OpenCV window showing exactly what the AI sees, including bounding boxes and your active FOV circle. Use this for tuning your settings in the practice range.
* **`best.pt`**: Your trained YOLOv5 PyTorch weights (detects the enemy outlines).
* **`requirements.txt`**: Python dependencies required to run the scripts.
* **`venv/`**: Your isolated Python virtual environment.

## 🚀 Setup & Execution

1. **Activate your virtual environment:**
```bash
# On Windows:
venv\Scripts\activate

```


2. **Install dependencies (if not already done):**
```bash
pip install -r requirements.txt

```


3. **Run the application:**
* For debugging and tuning: `python main_debug.py`
* For stealth performance: `python main.py`



## ⌨️ Controls

* **[`] (Tilde/Grave)**: Toggle Auto Aim (Smooth tracking)
* **[CTRL]**: Toggle Auto Shot (Triggerbot)
* **[UP ARROW]**: Increase FOV (Activation Range)
* **[DOWN ARROW]**: Decrease FOV (Activation Range)
* **[PAUSE]**: Emergency kill-switch to shut down the script safely.

---

## ⚙️ Configuration Variables (Tweaking Guide)

Open `main.py` or `main_debug.py` in a text editor to adjust these variables located near the top of the file.

### 🌐 Network Settings

* `BOARD_IP = "192.168.0.1"`: The local IP address of your NXP board.
* `UDP_PORT = 5005`: The UDP port your NXP board is listening on.

### 🎯 Aiming & Logic Settings

* `SENS = 0.85`
* **What it does:** Should match your exact in-game mouse sensitivity.
* **How to tweak:** The script uses this to calculate `AIM_SPEED` (`1 / SENS`). If the script moves your crosshair way too far past the enemy, increase this number. If it barely moves your crosshair at all, decrease this number.


* `DEADZONE = 4`
* **What it does:** Creates a tiny pixel radius around the exact center of the enemy's head where the mouse will stop trying to move.
* **How to tweak:** If your crosshair infinitely vibrates or shakes when resting on a stationary enemy, **increase** this to 5 or 6.


* `FIRE_DELAY = 0.15`
* **What it does:** The cooldown time (in seconds) between Auto Shot clicks.
* **How to tweak:** `0.15` equals roughly 6 shots per second. Decrease to `0.05` for faster bursting, or increase to `0.3` for slow, controlled tapping.



### 🖥️ Vision Settings

* `MONITOR_WIDTH = 1920` & `MONITOR_HEIGHT = 1080`
* **What it does:** Must match your actual in-game resolution.


* `MONITOR_SCALE = 5`
* **What it does:** Determines the size of the "AI Vision Box" in the center of your screen. A scale of `5` on a 1920x1080 monitor means the AI only looks at a 384x216 pixel box in the dead center.
* **How to tweak:** Decrease to `4` or `3` to make the AI look at a larger area of your screen (wider FOV limit), but be aware this will drastically lower your FPS.



### 🌊 Smoothing Settings (The 120Hz Thread)

* `AUTO_AIM_SMOOTHNESS = 0.2`
* **What it does:** Controls how much of the distance to the target is covered per frame.
* **How to tweak:** * `0`: Disables smooth tracking entirely (reverts to classic instant "Silent Aim" snapping).
* `0.1`: Very slow, "legit" looking drag towards the target.
* `0.4`: Fast, aggressive, snappy lock-on.




* `AUTO_AIM_DAMPING = 0.75`
* **What it does:** Prevents the 120Hz action thread from over-predicting the enemy's location before the 50Hz camera captures the next frame.
* **How to tweak:** If the crosshair ping-pongs left and right rapidly while tracking a moving target, **increase** this closer to `1.0`. If the crosshair feels like it's dragging too far behind a moving target, **decrease** this closer to `0.5`.