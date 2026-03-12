import threading, subprocess, sys

def run_bot():
    subprocess.run([sys.executable, "bot.py"])

def run_dashboard():
    subprocess.run([sys.executable, "dashboard.py"])

t1 = threading.Thread(target=run_bot, daemon=True)
t2 = threading.Thread(target=run_dashboard, daemon=True)
t1.start()
t2.start()
t1.join()
t2.join()
