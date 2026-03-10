import torch
import cv2
import numpy as np
import time
import math
import keyboard
import threading
import socket
import json
import tkinter as tk
import pywintypes
import win32api
import win32con
import dxcam
import os
import warnings
from mss import mss

# --- SILENCE WARNINGS ---
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# --- NETWORK CONFIGURATION ---
BOARD_IP = "192.168.0.1"  # The IP address of your external NXP hardware board
UDP_PORT = 5005           # The network port used to send data to the NXP board

# --- LOGIC CONFIGURATION ---
SENS = 0.85               # Your exact in-game mouse sensitivity
AIM_SPEED = 1 * (1/SENS)  # Multiplier to convert screen pixels into physical mouse movements
DEADZONE = 2              # Pixel radius around the head where the mouse stops moving to prevent shaking
FIRE_DELAY = 0.15         # Seconds to wait between Auto Shots (0.15s = ~6 shots per second)
MONITOR_WIDTH = 1920      # Total width of your game resolution
MONITOR_HEIGHT = 1080     # Total height of your game resolution
MONITOR_SCALE = 5         # Divides monitor size to create the smaller, faster AI vision box (1920/5 = 384px)

# --- NEW AUTO AIM VARIABLES ---
AUTO_AIM_SMOOTHNESS = 0 # 0 = Original Instant Snap. >0 = Smooth tracking (0.1 to 0.4 is recommended).
AUTO_AIM_DAMPING = 0.75   # Prevents high-speed oscillations. 0.0 = No damping, 1.0 = Max prediction damping. Ignored if Smoothness is 0.

# Multipliers used to adjust aiming math depending on how much MONITOR_SCALE zoomed in
target_multiply = [0, 1.01, 1.025, 1.05, 1.05, 1.05, 1.05, 1.05, 1.05, 1.05, 1.05]

# --- STATE VARIABLES ---
activation_range = 100    # Radius of the FOV circle; targets must be inside this to trigger aim
auto_shot = False         # Master toggle for the Auto Shot feature
auto_aim = False          # Master toggle for the Auto Aim feature
is_running = True         # Keeps the background threads alive while True

# Thread-safe cooldown flags (prevents hotkeys from double-triggering when pressed)
auto_shot_toggle = [True] 
auto_aim_toggle = [True]  
no_fov_cooldown = [True]  
can_fire = [True]         # Controls the delay between consecutive shots
auto_aim_cooldown = [True] # Used ONLY if AUTO_AIM_SMOOTHNESS is set to 0

# --- THREAD COMMUNICATION VARIABLES ---
# These variables allow the 50Hz Vision thread to share target data with the 120Hz Action thread
target_lock = threading.Lock() # Prevents threads from reading/writing data at the exact same time to avoid crashes
target_found = False           # True if the AI currently sees a valid target
target_head_x = 0.0            # X coordinate of the target's head
target_head_y = 0.0            # Y coordinate of the target's head
target_box_xmin = 0            # Left edge of the target's body (for Auto Shot detection)
target_box_xmax = 0            # Right edge of the target's body
target_box_ymin = 0            # Top edge of the target's body
target_box_ymax = 0            # Bottom edge of the target's body
screenshot_center = [0, 0]     # The exact center pixel of the AI vision box

def cooldown(cooldown_bool, wait):
    time.sleep(wait)
    cooldown_bool[0] = True

def clamp(val, min_val=-127, max_val=127):
    # Enforces the -127 to +127 byte limit required by the NXP board protocol
    return max(min_val, min(int(val), max_val))

# --- NETWORK SETUP ---
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def send_udp(payload):
    # Sends the formatted JSON movement command to the NXP board
    try:
        data = json.dumps(payload).encode('utf-8')
        sock.sendto(data, (BOARD_IP, UDP_PORT))
    except Exception:
        pass

# --- ACTION THREAD (Runs at 120Hz for ultra-smooth mouse movements) ---
def action_worker():
    global auto_shot, auto_aim, activation_range
    global target_head_x, target_head_y

    while is_running:
        start_time = time.perf_counter()

        # 1. Check Hotkeys
        if keyboard.is_pressed('ctrl'): 
            if auto_shot_toggle[0]:
                auto_shot = not auto_shot
                auto_shot_label.config(text=f"Auto Shot: {'Active' if auto_shot else 'Unactive'}", fg='green' if auto_shot else 'red')
                auto_shot_toggle[0] = False
                threading.Thread(target=cooldown, args=(auto_shot_toggle, 0.2)).start()

        if keyboard.is_pressed('`'):
            if auto_aim_toggle[0]:
                auto_aim = not auto_aim
                auto_aim_label.config(text=f"Auto Aim: {'Active' if auto_aim else 'Unactive'}", fg='green' if auto_aim else 'red')
                auto_aim_toggle[0] = False
                threading.Thread(target=cooldown, args=(auto_aim_toggle, 0.2)).start()

        elif keyboard.is_pressed('up') and no_fov_cooldown[0]:
            activation_range += 5
            fov_label.config(text=f"FOV: {activation_range}")
            no_fov_cooldown[0] = False
            threading.Thread(target=cooldown, args=(no_fov_cooldown, 0.05)).start()

        elif keyboard.is_pressed('down') and no_fov_cooldown[0]:
            activation_range = max(0, activation_range - 5)
            fov_label.config(text=f"FOV: {activation_range}")
            no_fov_cooldown[0] = False
            threading.Thread(target=cooldown, args=(no_fov_cooldown, 0.05)).start()

        # 2. Grab latest target data safely
        with target_lock:
            found = target_found
            hx, hy = target_head_x, target_head_y
            xmin, xmax = target_box_xmin, target_box_xmax
            ymin, ymax = target_box_ymin, target_box_ymax

        # 3. Execution Logic
        if found:
            # Auto Shot Logic
            if auto_shot and screenshot_center[0] in range(int(xmin), int(xmax)) and screenshot_center[1] in range(int(ymin), int(ymax)):
                if can_fire[0]:
                    send_udp({"t": "m", "x": 0, "y": 0, "b": 1})
                    time.sleep(0.04) # Hold left click for 40ms
                    send_udp({"t": "m", "x": 0, "y": 0, "b": 0})
                    
                    can_fire[0] = False
                    threading.Thread(target=cooldown, args=(can_fire, FIRE_DELAY)).start()

            # Auto Aim Logic
            distance = math.dist([hx, hy], screenshot_center)
            if auto_aim and distance < activation_range:
                raw_x = hx - screenshot_center[0]
                raw_y = hy - screenshot_center[1]
                
                # BRANCH A: Original Instant Snap (If SMOOTHNESS is 0)
                if AUTO_AIM_SMOOTHNESS == 0:
                    if auto_aim_cooldown[0]:
                        xdif = raw_x * AIM_SPEED * target_multiply[MONITOR_SCALE]
                        ydif = raw_y * AIM_SPEED * target_multiply[MONITOR_SCALE]
                        send_udp({"t": "m", "x": clamp(xdif), "y": clamp(ydif), "b": 0})
                        
                        auto_aim_cooldown[0] = False
                        threading.Thread(target=cooldown, args=(auto_aim_cooldown, 0.2)).start()

                # BRANCH B: Smooth 120Hz Tracking (If SMOOTHNESS > 0)
                else:
                    if abs(raw_x) > DEADZONE or abs(raw_y) > DEADZONE:
                        
                        # Dynamically adjust smoothness based on distance (closer = slower)
                        smooth_factor_x = AUTO_AIM_SMOOTHNESS if abs(raw_x) > 15 else (AUTO_AIM_SMOOTHNESS / 5.0)
                        smooth_factor_y = AUTO_AIM_SMOOTHNESS if abs(raw_y) > 15 else (AUTO_AIM_SMOOTHNESS / 5.0)
                        
                        xdif = raw_x * AIM_SPEED * target_multiply[MONITOR_SCALE] * smooth_factor_x
                        ydif = raw_y * AIM_SPEED * target_multiply[MONITOR_SCALE] * smooth_factor_y
                        
                        send_udp({"t": "m", "x": clamp(xdif), "y": clamp(ydif), "b": 0})
                        
                        # Under-predict virtual target based on the Damping variable
                        with target_lock:
                            target_head_x -= (raw_x * smooth_factor_x) * AUTO_AIM_DAMPING
                            target_head_y -= (raw_y * smooth_factor_y) * AUTO_AIM_DAMPING

        # Maintain ~120Hz Loop (0.0083 seconds)
        elapsed = time.perf_counter() - start_time
        time.sleep(max(0, 0.0083 - elapsed))

# --- UI OVERLAY ---
def labels():
    global fps_label, auto_shot_label, auto_aim_label, fov_label
    root = tk.Tk()
    root.title("NXP Overlay")
    
    def setup_stealth(win):
        win.overrideredirect(True)
        win.lift()
        win.wm_attributes("-topmost", True)
        win.wm_attributes("-disabled", True)
        win.wm_attributes("-transparentcolor", "black")
        hWindow = pywintypes.HANDLE(int(win.frame(), 16))
        exStyle = win32con.WS_EX_COMPOSITED | win32con.WS_EX_LAYERED | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOPMOST | win32con.WS_EX_TRANSPARENT
        win32api.SetWindowLong(hWindow, win32con.GWL_EXSTYLE, exStyle)

    fps_label = tk.Label(text="FPS: 0", font=('Tahoma','10'), fg='white', bg='black')
    fps_label.master.geometry("+14+16")
    setup_stealth(fps_label.master)
    fps_label.pack()

    fov_label = tk.Label(text=f"FOV: {activation_range}", font=('Tahoma','10'), fg='white', bg='black')
    fov_label.master.geometry("+14+36")
    setup_stealth(fov_label.master)
    fov_label.pack()

    auto_shot_label = tk.Label(text="Auto Shot: Unactive", font=('Tahoma','10'), fg='red', bg='black')
    auto_shot_label.master.geometry("+14+56")
    setup_stealth(auto_shot_label.master)
    auto_shot_label.pack()

    auto_aim_label = tk.Label(text="Auto Aim: Unactive", font=('Tahoma','10'), fg='red', bg='black')
    auto_aim_label.master.geometry("+14+76")
    setup_stealth(auto_aim_label.master)
    auto_aim_label.pack()

    root.mainloop()

# --- VISION THREAD (Main Loop, runs at max speed ~50Hz) ---
def main():
    global activation_range, is_running, screenshot_center
    global target_found, target_head_x, target_head_y, target_box_xmin, target_box_xmax, target_box_ymin, target_box_ymax

    threading.Thread(target=labels, daemon=True).start()
    threading.Thread(target=action_worker, daemon=True).start()

    print("Loading YOLOv5...")
    try:
        model = torch.hub.load('ultralytics/yolov5', 'custom', path='best_new.engine', trust_repo=True)
        print("TensorRT Engine loaded.")
    except:
        model = torch.hub.load('ultralytics/yolov5', 'custom', path='best.pt', trust_repo=True)
        print("Standard PyTorch model loaded.")
        
    model.conf = 0.25
    model.maxdet = 10
    model.classes = [1]
    
    if torch.cuda.is_available():
        model.cuda().half()

    # Capture Region setup
    region = (
        int(MONITOR_WIDTH/2 - MONITOR_WIDTH/MONITOR_SCALE/2),
        int(MONITOR_HEIGHT/2 - MONITOR_HEIGHT/MONITOR_SCALE/2),
        int(MONITOR_WIDTH/2 + MONITOR_WIDTH/MONITOR_SCALE/2),
        int(MONITOR_HEIGHT/2 + MONITOR_HEIGHT/MONITOR_SCALE/2)
    )
    x, y, width, height = region
    screenshot_center = [int((width-x)/2), int((height-y)/2)]

    # Screen Capture Initialization
    camera = None
    use_mss = False
    try:
        camera = dxcam.create(output_idx=0, output_color="BGR")
        if camera is None: raise Exception("DXCAM returned None")
    except Exception as e:
        print(f"DXCAM error: {e}. Falling back to MSS...")
        use_mss = True
        sct = mss()
        mss_region = {
            "top": region[1],
            "left": region[0],
            "width": region[2] - region[0],
            "height": region[3] - region[1]
        }

    start_time = time.time()
    counter = 0

    print("System Ready.")
    print("Hotkeys: [ ` ] = Auto Aim | [ CTRL ] = Auto Shot | [ UP/DOWN ] = FOV")

    cv2.namedWindow("AI Vision Debug")
    cv2.setWindowProperty("AI Vision Debug", cv2.WND_PROP_TOPMOST, 1)

    try:
        while True:
            # CAPTURE IMAGE
            if not use_mss:
                screenshot = camera.grab(region)
            else:
                screenshot = np.array(sct.grab(mss_region))
                screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR)
                
            if screenshot is None: continue

            # AI INFERENCE
            df = model(screenshot, size=384).pandas().xyxy[0]

            # FPS Tracker
            counter += 1
            if (time.time() - start_time) > 1.0:
                fps_label.config(text=f"FPS: {counter}")
                counter = 0
                start_time = time.time()

            closest_part_distance = 100000
            closest_part = -1

            # FIND CLOSEST TARGET
            for i in range(len(df)):
                try:
                    xmin = int(df.iloc[i,0])
                    ymin = int(df.iloc[i,1])
                    xmax = int(df.iloc[i,2])
                    ymax = int(df.iloc[i,3])

                    centerX = (xmax-xmin)/2 + xmin 
                    centerY = (ymax-ymin)/2 + ymin

                    distance = math.dist([centerX, centerY], screenshot_center)

                    if int(distance) < closest_part_distance:
                        closest_part_distance = distance
                        closest_part = i
                    
                    # Debug Drawings
                    cv2.rectangle(screenshot, (xmin, ymin), (xmax, ymax), (0, 255, 0), 2)
                    cv2.circle(screenshot, (int(centerX), int(centerY)), 3, (0, 0, 255), -1)
                except:
                    pass

            # UPDATE SHARED DATA FOR ACTION THREAD
            with target_lock:
                if closest_part != -1:
                    target_found = True
                    target_box_xmin = df.iloc[closest_part,0]
                    target_box_ymin = df.iloc[closest_part,1]
                    target_box_xmax = df.iloc[closest_part,2]
                    target_box_ymax = df.iloc[closest_part,3]
                    
                    target_head_x = (target_box_xmax - target_box_xmin) / 2 + target_box_xmin
                    target_head_y = (target_box_ymax - target_box_ymin) / 2 + target_box_ymin
                else:
                    target_found = False

            # Draw FOV & Show Debug Window
            cv2.circle(screenshot, (screenshot_center[0], screenshot_center[1]), activation_range, (255, 255, 0), 1)
            cv2.imshow("AI Vision Debug", screenshot)
            cv2.waitKey(1)

            # Failsafe
            if keyboard.is_pressed('pause'):
                break
                
    finally:
        is_running = False
        cv2.destroyAllWindows()
        print("Shutting down...")

if __name__ == "__main__":
    main()