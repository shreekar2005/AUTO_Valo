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
BOARD_IP = "192.168.0.1"  # Update with your NXP board's IP
UDP_PORT = 5005           # Update with your NXP board's Port

# --- LOGIC CONFIGURATION ---
SENS = 0.85
HUMANIZE = 0.5
AIM_SPEED = 1 * (1 / SENS)
MONITOR_WIDTH = 1920
MONITOR_HEIGHT = 1080
MONITOR_SCALE = 5 
target_multiply = [0, 1.01, 1.025, 1.05, 1.05, 1.05, 1.05, 1.05, 1.05, 1.05, 1.05]

# --- STATE VARIABLES ---
activation_range = 100
auto_shot = False
auto_aim = False
is_running = True

# Thread-safe cooldown flags
auto_shot_toggle = [True]
auto_aim_toggle = [True]
no_fov_cooldown = [True]
auto_aim_not_cooldown = [True]

def cooldown(cooldown_bool, wait):
    time.sleep(wait)
    cooldown_bool[0] = True

# Helper to keep mouse movements within NXP -127 to +127 byte limits
def clamp(val, min_val=-127, max_val=127):
    return max(min_val, min(int(val), max_val))

# --- NETWORK SETUP & WORKER ---
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
input_queue = []

def send_udp(payload):
    try:
        data = json.dumps(payload).encode('utf-8')
        sock.sendto(data, (BOARD_IP, UDP_PORT))
    except Exception as e:
        pass

def input_worker():
    while is_running:
        start_time = time.perf_counter()
        if input_queue:
            payload = input_queue.pop(0)
            send_udp(payload)
            
            # If it's a click, automatically send the release packet shortly after
            if payload.get("b") == 1:
                time.sleep(0.04) # Hold click for 40ms
                send_udp({"t": "m", "x": 0, "y": 0, "b": 0})
        
        # Cap at roughly 200Hz to avoid flooding
        elapsed = time.perf_counter() - start_time
        time.sleep(max(0, 0.005 - elapsed))

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

# --- MAIN LOOP ---
def main():
    global auto_shot, auto_aim, activation_range, is_running

    threading.Thread(target=labels, daemon=True).start()
    threading.Thread(target=input_worker, daemon=True).start()

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

    # Capture Region
    region = (
        int(MONITOR_WIDTH/2 - MONITOR_WIDTH/MONITOR_SCALE/2),
        int(MONITOR_HEIGHT/2 - MONITOR_HEIGHT/MONITOR_SCALE/2),
        int(MONITOR_WIDTH/2 + MONITOR_WIDTH/MONITOR_SCALE/2),
        int(MONITOR_HEIGHT/2 + MONITOR_HEIGHT/MONITOR_SCALE/2)
    )
    x, y, width, height = region
    screenshot_center = [int((width-x)/2), int((height-y)/2)]

    # --- SCREEN CAPTURE INITIALIZATION (DXCAM with MSS Fallback) ---
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

    # +++ INITIALIZE DEBUG WINDOW AS ALWAYS ON TOP +++
    cv2.namedWindow("AI Vision Debug")
    cv2.setWindowProperty("AI Vision Debug", cv2.WND_PROP_TOPMOST, 1)

    try:
        while True:
            closest_part_distance = 100000
            closest_part = -1
            
            # --- CAPTURE LOGIC ---
            if not use_mss:
                screenshot = camera.grab(region)
            else:
                screenshot = np.array(sct.grab(mss_region))
                screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR) # MSS uses BGRA
                
            if screenshot is None: continue

            # Inference
            df = model(screenshot, size=384).pandas().xyxy[0]

            # FPS Counter
            counter += 1
            if (time.time() - start_time) > 1.0:
                fps_label.config(text=f"FPS: {counter}")
                counter = 0
                start_time = time.time()

            # Target Selection & Debug Drawing
            for i in range(len(df)):
                try:
                    xmin = int(df.iloc[i,0])
                    ymin = int(df.iloc[i,1])
                    xmax = int(df.iloc[i,2])
                    ymax = int(df.iloc[i,3])

                    centerX = (xmax-xmin)/2 + xmin 
                    centerY = (ymax-ymin)/2 + ymin

                    # VISUAL DEBUGGING OVERLAY
                    cv2.rectangle(screenshot, (xmin, ymin), (xmax, ymax), (0, 255, 0), 2)
                    cv2.circle(screenshot, (int(centerX), int(centerY)), 3, (0, 0, 255), -1)

                    distance = math.dist([centerX, centerY], screenshot_center)

                    if int(distance) < closest_part_distance:
                        closest_part_distance = distance
                        closest_part = i
                except:
                    pass

            # DRAW FOV CIRCLE
            cv2.circle(screenshot, (screenshot_center[0], screenshot_center[1]), activation_range, (255, 255, 0), 1)

            # SHOW THE DEBUG WINDOW
            cv2.imshow("AI Vision Debug", screenshot)
            cv2.waitKey(1)

            # --- HOTKEYS ---
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

            # --- UDP EXECUTION LOGIC ---
            if closest_part != -1:
                xmin = df.iloc[closest_part,0]
                ymin = df.iloc[closest_part,1]
                xmax = df.iloc[closest_part,2]
                ymax = df.iloc[closest_part,3]
                head_center_list = [(xmax-xmin)/2+xmin, (ymax-ymin)/2+ymin]

                # 1. Auto Shot
                if auto_shot and screenshot_center[0] in range(int(xmin), int(xmax)) and screenshot_center[1] in range(int(ymin), int(ymax)):
                    input_queue.append({"t": "m", "x": 0, "y": 0, "b": 1})

                # 2. Auto Aim (Raw snap)
                if auto_aim and closest_part_distance < activation_range and auto_aim_not_cooldown[0]:
                    xdif = (head_center_list[0] - screenshot_center[0]) * AIM_SPEED * target_multiply[MONITOR_SCALE]
                    ydif = (head_center_list[1] - screenshot_center[1]) * AIM_SPEED * target_multiply[MONITOR_SCALE]
                    
                    input_queue.append({"t": "m", "x": clamp(xdif), "y": clamp(ydif), "b": 0})
                    
                    auto_aim_not_cooldown[0] = False
                    threading.Thread(target=cooldown, args=(auto_aim_not_cooldown, 0.2)).start()

            # Failsafe
            if keyboard.is_pressed('pause'):
                break
                
    finally:
        is_running = False
        cv2.destroyAllWindows()
        print("Shutting down...")

if __name__ == "__main__":
    main()