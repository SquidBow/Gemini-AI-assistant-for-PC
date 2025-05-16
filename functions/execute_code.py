import subprocess
import sys
import os

# Add these imports at the top if not present
from pathlib import Path
import time
import json

# Import background process tracking from function_calls.py
from functions.function_calls import BACKGROUND_PROCESSES, save_background_processes
# --- Background process tracking ---
def execute_code(code, filename="temp_exec.py", args=None, background=False):
    """
    Executes a Python code string and returns the output.
    Args:
        code (str): The Python code to execute.
        filename (str): The temporary filename to use.
        args (list or str): Arguments to pass to the script.
        background (bool): If True, run in background and return the PID.
    """
    with open(filename, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        arg_list = []
        if args:
            if isinstance(args, str):
                arg_list = [args]
            elif isinstance(args, list):
                arg_list = args
        cmd = [sys.executable, filename] + arg_list

        if background:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # --- Track background process ---
            abs_path = str(Path(filename).resolve())
            BACKGROUND_PROCESSES[abs_path] = {
                "pid": proc.pid,
                "time_to_execute": 0,
                "background": True,
                "type": ".py"
            }
            save_background_processes()
            return f"Started code in background with PID {proc.pid}."
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout + ("\n" + result.stderr if result.stderr else "")
            return output.strip()
    except Exception as e:
        return f"Error executing code: {e}"
    finally:
        # Only remove the file if not running in background
        if not background:
            try:
                os.remove(filename)
            except Exception:
                pass