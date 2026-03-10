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
BOARD_IP = "192.168.0.1"  
UDP_PORT = 5005           

# --- LOGIC CONFIGURATION ---
SENS = 0.85               
AIM_SPEED = 1 * (1/SENS)  
DEADZONE = 2             
FIRE_DELAY = 0.15         
MONITOR_WIDTH = 1920      
MONITOR_HEIGHT = 1080     
MONITOR_SCALE = 5         

# --- AUTO AIM VARIABLES ---
AUTO_AIM_SMOOTHNESS = 0 # increase this to increase smoothness (0.1 is recommanded)
AUTO_AIM_DAMPING = 0.9   

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
can_fire = [True]         
auto_aim_cooldown = [True] 

# --- THREAD COMMUNICATION VARIABLES ---
target_lock = threading.Lock() 
target_found = False           
target_head_x = 0.0            
target_head_y = 0.0            
target_box_xmin = 0            
target_box_xmax = 0            
target_box_ymin = 0            
target_box_ymax = 0            
screenshot_center = [0, 0]     

def cooldown(cooldown_bool, wait):
    time.sleep(wait)
    cooldown_bool[0] = True

def clamp(val, min_val=-127, max_val=127):
    return max(min_val, min(int(val), max_val))

# --- NETWORK SETUP ---
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def send_udp(payload):
    try:
        data = json.dumps(payload).encode('utf-8')
        sock.sendto(data, (BOARD_IP, UDP_PORT))
    except Exception:
        pass

# --- ACTION THREAD (120Hz) ---
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
                    time.sleep(0.04) 
                    send_udp({"t": "m", "x": 0, "y": 0, "b": 0})
                    
                    can_fire[0] = False
                    threading.Thread(target=cooldown, args=(can_fire, FIRE_DELAY)).start()

            # Auto Aim Logic
            distance = math.dist([hx, hy], screenshot_center)
            if auto_aim and distance < activation_range:
                raw_x = hx - screenshot_center[0]
                raw_y = hy - screenshot_center[1]
                
                if AUTO_AIM_SMOOTHNESS == 0:
                    if auto_aim_cooldown[0]:
                        xdif = raw_x * AIM_SPEED * target_multiply[MONITOR_SCALE]
                        ydif = raw_y * AIM_SPEED * target_multiply[MONITOR_SCALE]
                        send_udp({"t": "m", "x": clamp(xdif), "y": clamp(ydif), "b": 0})
                        
                        auto_aim_cooldown[0] = False
                        threading.Thread(target=cooldown, args=(auto_aim_cooldown, 0.2)).start()
                else:
                    if abs(raw_x) > DEADZONE or abs(raw_y) > DEADZONE:
                        smooth_factor_x = AUTO_AIM_SMOOTHNESS if abs(raw_x) > 15 else (AUTO_AIM_SMOOTHNESS / 5.0)
                        smooth_factor_y = AUTO_AIM_SMOOTHNESS if abs(raw_y) > 15 else (AUTO_AIM_SMOOTHNESS / 5.0)
                        
                        xdif = raw_x * AIM_SPEED * target_multiply[MONITOR_SCALE] * smooth_factor_x
                        ydif = raw_y * AIM_SPEED * target_multiply[MONITOR_SCALE] * smooth_factor_y
                        
                        send_udp({"t": "m", "x": clamp(xdif), "y": clamp(ydif), "b": 0})
                        
                        with target_lock:
                            target_head_x -= (raw_x * smooth_factor_x) * AUTO_AIM_DAMPING
                            target_head_y -= (raw_y * smooth_factor_y) * AUTO_AIM_DAMPING

        elapsed = time.perf_counter() - start_time
        time.sleep(max(0, 0.0083 - elapsed))

# --- UI OVERLAY ---
def labels():
    global fps_label, auto_shot_label, auto_aim_label, fov_label, status_label
    root = tk.Tk()
    root.title("NXP Overlay")
    
    # Setup single transparent, click-through window
    root.overrideredirect(True)
    root.lift()
    root.wm_attributes("-topmost", True)
    root.wm_attributes("-disabled", True)
    root.wm_attributes("-transparentcolor", "black")
    root.config(bg="black")
    root.geometry("+14+16")

    hWindow = pywintypes.HANDLE(int(root.frame(), 16))
    exStyle = win32con.WS_EX_COMPOSITED | win32con.WS_EX_LAYERED | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOPMOST | win32con.WS_EX_TRANSPARENT
    win32api.SetWindowLong(hWindow, win32con.GWL_EXSTYLE, exStyle)

    # Stack labels cleanly
    status_label = tk.Label(root, text="Status: Loading Model...", font=('Tahoma','10', 'bold'), fg='yellow', bg='black')
    status_label.pack(anchor="w")

    fps_label = tk.Label(root, text="FPS: 0", font=('Tahoma','10'), fg='white', bg='black')
    fps_label.pack(anchor="w")

    fov_label = tk.Label(root, text=f"FOV: {activation_range}", font=('Tahoma','10'), fg='white', bg='black')
    fov_label.pack(anchor="w")

    auto_shot_label = tk.Label(root, text="Auto Shot: Unactive", font=('Tahoma','10'), fg='red', bg='black')
    auto_shot_label.pack(anchor="w")

    auto_aim_label = tk.Label(root, text="Auto Aim: Unactive", font=('Tahoma','10'), fg='red', bg='black')
    auto_aim_label.pack(anchor="w")

    root.mainloop()

# --- VISION THREAD (50Hz) ---
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

    region = (
        int(MONITOR_WIDTH/2 - MONITOR_WIDTH/MONITOR_SCALE/2),
        int(MONITOR_HEIGHT/2 - MONITOR_HEIGHT/MONITOR_SCALE/2),
        int(MONITOR_WIDTH/2 + MONITOR_WIDTH/MONITOR_SCALE/2),
        int(MONITOR_HEIGHT/2 + MONITOR_HEIGHT/MONITOR_SCALE/2)
    )
    x, y, width, height = region
    screenshot_center = [int((width-x)/2), int((height-y)/2)]

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
    
    # Update UI to show system is active
    time.sleep(1) # Give Tkinter a moment to initialize
    try:
        status_label.config(text="Status: Active", fg="#00FF00")
    except:
        pass

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
                except:
                    pass

            # UPDATE SHARED DATA
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

            if keyboard.is_pressed('pause'):
                break
                
    finally:
        is_running = False
        print("Shutting down...")

if __name__ == "__main__":
    main()