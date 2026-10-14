'''Staggered launch of the 4 parallel store ingests (avoids the
simultaneous-start DLL-init failure 0xC0000142), then the finisher.'''
import subprocess
import time

STORES = ["lp_118", "lp_18", "lp_159", "lp_69"]
DELAY_S = 240

for qid in STORES:
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Start-ScheduledTask -TaskName 'lpcamp-one-{qid}'"],
                   capture_output=True, text=True, timeout=60)
    print(f"started {qid} at {time.strftime('%H:%M:%S')}", flush=True)
    if qid != STORES[-1]:
        time.sleep(DELAY_S)

subprocess.run(["powershell", "-NoProfile", "-Command",
                "Start-ScheduledTask -TaskName 'lpcamp-finisher'"],
               capture_output=True, text=True, timeout=60)
print(f"started finisher at {time.strftime('%H:%M:%S')}", flush=True)
