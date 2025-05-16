import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from PIL import Image
from PIL import ImageEnhance
import base64
from io import BytesIO
import psutil
import chardet
import json
import sys
import py_compile


KNOWN_FILES_PATH = Path("known_files.txt")
BACKUP_FOLDER = Path(r"C:\Документи\Backups")
BACKGROUND_PROCESSES_FILE = Path("background_processes.json")
BACKGROUND_PROCESSES = {}

def save_background_processes():
    """internal"""
    try:
        with BACKGROUND_PROCESSES_FILE.open("w", encoding="utf-8") as f:
            json.dump(BACKGROUND_PROCESSES, f)
    except Exception as e:
        print(f"[ERROR] Saving background processes: {e}")

def load_background_processes():
    """internal"""
    global BACKGROUND_PROCESSES
    if BACKGROUND_PROCESSES_FILE.exists():
        try:
            with BACKGROUND_PROCESSES_FILE.open("r", encoding="utf-8") as f:
                BACKGROUND_PROCESSES.update(json.load(f))
        except Exception as e:
            print(f"[ERROR] Loading background processes: {e}")

def add_known_file(filepath):
    """internal"""
    filepath = str(Path(filepath).resolve())
    KNOWN_FILES_PATH.touch(exist_ok=True)
    with KNOWN_FILES_PATH.open("r+", encoding="utf-8") as f:
        files = set(line.strip() for line in f)
        if filepath not in files:
            f.write(filepath + "\n")

def remove_known_file(filepath):
    """internal"""
    filepath = str(Path(filepath).resolve())
    if not KNOWN_FILES_PATH.exists():
        return
    with KNOWN_FILES_PATH.open("r", encoding="utf-8") as f:
        files = set(line.strip() for line in f)
    files_to_remove = {f for f in files if f == filepath or f.startswith(filepath + os.sep)}
    files -= files_to_remove
    with KNOWN_FILES_PATH.open("w", encoding="utf-8") as f:
        for file in files:
            f.write(file + "\n")

def list_known_files():
    if not KNOWN_FILES_PATH.exists():
        return []
    with KNOWN_FILES_PATH.open("r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def backup_file(filepath):
    """internal"""
    try:
        BACKUP_FOLDER.mkdir(parents=True, exist_ok=True)
        src = Path(filepath)
        if src.exists():
            backup_path = BACKUP_FOLDER / src.name
            count = 1
            while backup_path.exists():
                backup_path = BACKUP_FOLDER / f"{src.stem}_backup{count}{src.suffix}"
                count += 1
            shutil.copy2(src, backup_path)
    except Exception as e:
        print(f"[ERROR] Backup file: {e}")

def detect_encoding(filepath, sample_size=4096):
    """internal"""
    with open(filepath, "rb") as f:
        raw = f.read(sample_size)
    return chardet.detect(raw)["encoding"] or "utf-8"

def try_read_file_with_encodings(filepath, encodings=None):
    """internal"""
    if encodings is None:
        encodings = ["utf-8-sig", "utf-8", "utf-16", "cp1251", "cp1252", "latin1"]
    for enc in encodings:
        try:
            with open(filepath, "r", encoding=enc) as f:
                return f.read()
        except Exception:
            continue
    enc = detect_encoding(filepath)
    try:
        with open(filepath, "rb") as f:
            raw = f.read()
        return raw.decode(enc, errors="replace")
    except Exception:
        return None

def create_file(filepath, contents=None, append=False):
    # If the path ends with a slash or backslash, create a folder
    if filepath.endswith("/") or filepath.endswith("\\"):
        path = Path(os.path.expanduser(filepath)).resolve()
        path.mkdir(parents=True, exist_ok=True)
        add_known_file(path)
        return f"Created folder: {path}"
    try:
        path = Path(os.path.expanduser(filepath)).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        backup_file(path)
        encoding = "utf-8-sig" if path.suffix.lower() == ".ps1" else "utf-8"
        if isinstance(contents, str):
            # Only decode escapes if it doesn't look like code
            if not ("def " in contents or "import " in contents or "class " in contents):
                if (contents.startswith('"') and contents.endswith('"')) or (contents.startswith("'") and contents.endswith("'")):
                    contents = contents[1:-1]
                try:
                    contents = contents.encode("utf-8").decode("unicode_escape")
                except Exception:
                    pass
        if path.suffix.lower() == ".bat" and contents and "chcp 65001" not in contents:
            contents = "chcp 65001\n" + contents
        with path.open(mode, encoding=encoding) as f:
            f.write(contents if contents is not None else "")
        content = try_read_file_with_encodings(path)
        if content is None:
            return f"Error reading file with any known encoding: {path}"
        add_known_file(path)
        abs_path = str(path)
        proc_info = BACKGROUND_PROCESSES.get(abs_path)
        if proc_info and psutil.pid_exists(proc_info["pid"]):
            terminate_execution(abs_path)
            execute(abs_path, time_to_execute=proc_info.get("time_to_execute", 0), background=True)
            return f"{'Appended to' if append else 'Created'} file: {filepath}. Relaunched running process."
        return f"{'Appended to' if append else 'Created'} file: {filepath}"
    except PermissionError:
        return f"Permission denied: Cannot write to {filepath}. Try running as administrator."
    except Exception as e:
        return f"Error creating/appending file: {e}"

def edit_file(filepath, old_block, new_block):
    try:
        path = Path(os.path.expanduser(filepath)).resolve()
        if not path.exists():
            return f"Error: File does not exist: {path}"
        backup_file(path)
        content = try_read_file_with_encodings(path)
        if content is None:
            return f"Error reading file with any known encoding: {path}"

        # Only decode escapes if it doesn't look like code
        for var_name in ["old_block", "new_block"]:
            val = locals()[var_name]
            if isinstance(val, str) and not ("def " in val or "import " in val or "class " in val):
                try:
                    locals()[var_name] = val.encode("utf-8").decode("unicode_escape")
                except Exception:
                    pass

        old_block = locals()["old_block"]
        new_block = locals()["new_block"]

        if old_block not in content:
            return "Error: Old block not found in file."
        new_content = content.replace(old_block, new_block, 1)
        with path.open("w", encoding="utf-8") as f:
            f.write(new_content)
        abs_path = str(path)
        proc_info = BACKGROUND_PROCESSES.get(abs_path)
        if proc_info and psutil.pid_exists(proc_info["pid"]):
            terminate_execution(abs_path)
            execute(abs_path, time_to_execute=proc_info.get("time_to_execute", 0), background=True)
            return "File edited successfully. Relaunched running process."
        return "File edited successfully."
    except Exception as e:
        return f"Error editing file: {e}"

def delete(path):
    abs_path = str(Path(os.path.expanduser(path)).resolve())
    try:
        p = Path(abs_path)
        if p.is_dir():
            for file in p.rglob("*"):
                if file.is_file():
                    backup_file(file)
            shutil.rmtree(p)
            remove_known_file(abs_path)
            return f"Deleted folder: {abs_path}"
        elif p.is_file():
            backup_file(p)
            p.unlink()
            remove_known_file(abs_path)
            return f"Deleted file: {abs_path}"
        else:
            return f"Path does not exist: {abs_path}"
    except Exception as e:
        return f"Error deleting: {e}"

def read(path="Path to the image/file/folder"):
    if not path:
        return "Error: No path provided."
    abs_path = str(Path(os.path.expanduser(path)).resolve())
    p = Path(abs_path)
    if not p.exists():
        return f"Error: Path does not exist: {abs_path}"

    if p.is_dir():
        items = [item.name for item in p.iterdir()]
        for item in p.iterdir():
            add_known_file(item)
        add_known_file(p)
        if not items:
            return {
                "type": "directory",
                "path": abs_path,
                "items": [],
                "message": "Directory is empty."
            }
        return {
            "type": "directory",
            "path": abs_path,
            "items": items
        }

    # Try image first
    try:
        img = Image.open(p)
        img.verify()  # Verify it's a valid image
        img = Image.open(p)  # Reopen after verify (PIL quirk)
        if img.format not in ["PNG", "JPEG", "JPG", "BMP", "GIF"]:
            raise ValueError("Unsupported image format")
        img = img.convert("RGB")  # Ensure compatibility for ASCII art
        img = ImageEnhance.Brightness(img).enhance(0.5)
        try:
            ascii_art = image_to_ascii_color(img)
        except Exception:
            ascii_art = "[Could not generate ASCII art for this image.]"
        info = {
            "type": "image",
            "path": abs_path,
            "format": img.format,
            "size": img.size,
            "mode": img.mode,
            "ascii_art": ascii_art
        }
        add_known_file(abs_path)
        return info
    except Exception:
        pass  # Not an image, try as text

    # Try text
    content = try_read_file_with_encodings(abs_path)
    if content is not None:
        TOKEN_LIMIT = 90000
        if len(content) > TOKEN_LIMIT:
            content = content[:TOKEN_LIMIT] + "\n[token limit reached, first 90 000 tokens shown]"
        add_known_file(abs_path)
        if not content.strip():
            return {
                "type": "text",
                "path": abs_path,
                "content": "",
                "message": "File is empty."
            }
        return {
            "type": "text",
            "path": abs_path,
            "content": content
        }

    # Fallback: binary sample
    try:
        with open(abs_path, "rb") as f:
            data = f.read(128)
        add_known_file(abs_path)
        return {
            "type": "binary",
            "path": abs_path,
            "size": os.path.getsize(abs_path),
            "sample": base64.b64encode(data).decode("utf-8")
        }
    except Exception as e:
        return f"Error reading file: {e}"

def execute(filepath, time_to_execute=0, background=False, new_window=False, arguments=None):
    abs_path = str(Path(os.path.expanduser(filepath)).resolve())
    p = Path(abs_path)
    if not p.exists():
        return f"Error: File does not exist: {abs_path}"

    ext = p.suffix.lower()
    class_name = p.stem

    # Prepare arguments
    arg_list = []
    if arguments:
        if isinstance(arguments, str):
            # Split the string into arguments (handles quoted strings)
            import shlex
            arg_list = shlex.split(arguments)
        elif isinstance(arguments, list):
            arg_list = arguments

    # --- Compile Java file first if necessary ---
    if ext == ".java":
        compile_cmd = ["javac", abs_path]
        try:
            compile_proc = subprocess.run(
                compile_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                shell=False,  # Safer not to use shell for compilation
                check=False  # Don't raise exception on non-zero exit
            )
            if compile_proc.returncode != 0:
                return f"Java compilation failed:\nSTDOUT:\n{compile_proc.stdout}\nSTDERR:\n{compile_proc.stderr}"
            print(f"[DEBUG] Java file compiled successfully: {abs_path}")
        except FileNotFoundError:
            return "Error: 'javac' command not found. Make sure JDK is installed and in your system's PATH."
        except Exception as e:
            return f"Error during Java compilation: {e}"
    # --- End Java compilation ---

    # --- Handle new_window specifically for Python and Batch --- 
    if new_window:
        try:
            if ext == ".py":
                proc = subprocess.Popen(
                    [sys.executable, abs_path],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    cwd=p.parent
                )
            elif ext == ".bat":
                proc = subprocess.Popen(
                    ["cmd.exe", "/c", "start", "", abs_path],
                    cwd=p.parent
                )
            elif ext == ".exe":
                proc = subprocess.Popen(
                    ["cmd.exe", "/c", "start", "", abs_path],
                    cwd=p.parent
                )
            elif ext == ".ps1":
                proc = subprocess.Popen(
                    ["powershell", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", abs_path],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    cwd=p.parent
                )
            else:
                proc = subprocess.Popen(
                    ["cmd.exe", "/c", "start", "", abs_path],
                    cwd=p.parent
                )
            return f"Opened {filepath} in a new window (PID {proc.pid})."
        except Exception as e:
            return f"Error starting {filepath} in new window: {e}"

    # --- Foreground execution function ---
    def run_process():
        cmd = None
        if ext == ".bat":
            cmd = [abs_path] + arg_list
        elif ext == ".ps1":
            cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", abs_path] + arg_list
        elif ext == ".exe":
            cmd = [abs_path] + arg_list
        elif ext == ".py":
            cmd = [sys.executable, abs_path] + arg_list
        elif ext == ".java":
            cmd = ["java", "-cp", str(p.parent), class_name] + arg_list
        else:
            cmd = [abs_path] + arg_list

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            use_shell = (ext == ".bat")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                shell=use_shell,
                env=env,
                cwd=p.parent
            )

            if time_to_execute == 0:
                try:
                    out, err = proc.communicate(timeout=60)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, err = proc.communicate()
                    return f"Process timed out after 60 seconds and was killed.\nOutput:\n{out}\nError:\n{err}".strip()

                if proc.returncode == 0 and not out.strip() and not err.strip():
                    return "Execution successful (no output)."
                result = ""
                if out.strip():
                    result += f"Execution output:\n{out.strip()}\n"
                if err.strip():
                    result += f"Execution error:\n{err.strip()}\n"
                if not result:
                    result = f"Execution finished with code {proc.returncode} (no output or error)."
                return result.strip()

            elif time_to_execute == -1:
                out, err = proc.communicate()
                if proc.returncode == 0 and not out.strip() and not err.strip():
                    return "Execution successful (no output)."
                return f"Execution output:\n{out}\nExecution error:\n{err}".strip()
            else:
                try:
                    out, err = proc.communicate(timeout=time_to_execute)
                    if proc.returncode == 0 and not out.strip() and not err.strip():
                        return f"Execution successful (no output, ran for up to {time_to_execute} seconds)."
                    return f"Execution output:\n{out}\nExecution error:\n{err}".strip()
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, err = proc.communicate()
                    timeout_msg = f"Process killed after {time_to_execute} seconds."
                    result = timeout_msg + "\n"
                    if out.strip():
                        result += f"Output before timeout:\n{out.strip()}\n"
                    if err.strip():
                        result += f"Error before timeout:\n{err.strip()}\n"
                    return result.strip()
        except FileNotFoundError:
            if ext == ".java" and cmd and cmd[0] == "java":
                return "Error: 'java' command not found. Make sure JRE/JDK is installed and in your system's PATH."
            if ext == ".py" and cmd and cmd[0] == sys.executable:
                return f"Error: Python interpreter not found at '{sys.executable}' or not in PATH."
            return f"Error: Command not found for executing {filepath}. Ensure the required interpreter/runtime is installed and in PATH."
        except Exception as e:
            return f"Error executing file: {e}"
    # --- End foreground execution function ---

    # --- Background execution logic ---
    if background:
        try:
            cmd = None
            preflight_error = None

            if ext == ".py":
                # Pre-flight compile check for Python
                try:
                    py_compile.compile(abs_path, doraise=True)
                except py_compile.PyCompileError as e:
                    return f"Python script failed to compile:\n{e}"
                cmd = [sys.executable, abs_path]
            elif ext == ".bat":
                # Pre-flight: run with short timeout to catch syntax/runtime errors
                try:
                    test_proc = subprocess.run([abs_path], shell=True, cwd=p.parent, timeout=3, capture_output=True, text=True)
                    if test_proc.returncode != 0:
                        return f"Batch script failed pre-flight check:\nSTDOUT:\n{test_proc.stdout}\nSTDERR:\n{test_proc.stderr}"
                except Exception as e:
                    return f"Batch script failed pre-flight check: {e}"
                cmd = [abs_path]
            elif ext == ".ps1":
                # Pre-flight: run with short timeout to catch syntax/runtime errors
                try:
                    test_proc = subprocess.run(
                        ["powershell", "-ExecutionPolicy", "Bypass", "-File", abs_path],
                        cwd=p.parent, timeout=3, capture_output=True, text=True
                    )
                    if test_proc.returncode != 0:
                        return f"PowerShell script failed pre-flight check:\nSTDOUT:\n{test_proc.stdout}\nSTDERR:\n{test_proc.stderr}"
                except Exception as e:
                    return f"PowerShell script failed pre-flight check: {e}"
                cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", abs_path]
            elif ext == ".exe":
                # Pre-flight: run with short timeout to catch immediate errors
                try:
                    test_proc = subprocess.run([abs_path], cwd=p.parent, timeout=3, capture_output=True, text=True)
                    if test_proc.returncode != 0:
                        return f"Executable failed pre-flight check:\nSTDOUT:\n{test_proc.stdout}\nSTDERR:\n{test_proc.stderr}"
                except Exception as e:
                    return f"Executable failed pre-flight check: {e}"
                cmd = [abs_path]
            elif ext == ".java":
                if not (p.parent / f"{class_name}.class").exists():
                    compile_cmd = ["javac", abs_path]
                    compile_proc = subprocess.run(compile_cmd, capture_output=True, text=True, encoding="utf-8", check=False)
                    if compile_proc.returncode != 0:
                        return f"Background start failed: Java compilation failed:\n{compile_proc.stderr}"
                # Pre-flight: run with short timeout
                try:
                    test_proc = subprocess.run(
                        ["java", "-cp", str(p.parent), class_name],
                        cwd=p.parent, timeout=3, capture_output=True, text=True
                    )
                    if test_proc.returncode != 0:
                        return f"Java program failed pre-flight check:\nSTDOUT:\n{test_proc.stdout}\nSTDERR:\n{test_proc.stderr}"
                except Exception as e:
                    return f"Java program failed pre-flight check: {e}"
                cmd = ["java", "-cp", str(p.parent), class_name]
            else:
                # For other types, just check if file exists and is executable
                if not os.access(abs_path, os.X_OK):
                    return f"File {filepath} is not executable or not found."
                cmd = [abs_path]

            use_shell = (ext == ".bat")
            with open(os.devnull, "w") as devnull:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=use_shell,
                    cwd=p.parent
                )
                time.sleep(2)  # Wait for startup errors

                if proc.poll() is not None:
                    out, err = proc.communicate()
                    error_msg = (
                        f"Error: Background process for {filepath} exited immediately.\n"
                        f"Output:\n{out.decode(errors='replace')}\n"
                        f"Error:\n{err.decode(errors='replace')}"
                    )
                    # Save error to background_errors.json
                    try:
                        errors_path = Path("background_errors.json")
                        errors = []
                        if errors_path.exists():
                            with errors_path.open("r", encoding="utf-8") as f:
                                errors = json.load(f)
                        errors.append({"file": filepath, "error": error_msg, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                        with errors_path.open("w", encoding="utf-8") as f:
                            json.dump(errors, f, ensure_ascii=False, indent=2)
                    except Exception as e:
                        print(f"[ERROR] Saving background error: {e}")
                    return error_msg

            BACKGROUND_PROCESSES[abs_path] = {
                "pid": proc.pid,
                "time_to_execute": time_to_execute,
                "background": background,
                "type": ext
            }
            save_background_processes()
            return f"Started {filepath} in background with PID {proc.pid}."
        except FileNotFoundError:
            if ext == ".java" and cmd and cmd[0] == "java":
                return "Error starting background process: 'java' command not found."
            if ext == ".py" and cmd and cmd[0] == sys.executable:
                return f"Error starting background process: Python interpreter not found at '{sys.executable}'."
            return f"Error starting background process: Command not found for {filepath}."
        except Exception as e:
            return f"Error starting background process: {e}"
    else:
        return run_process()

def list_background_processes():
    running = []
    not_running = []
    for path, proc_info in BACKGROUND_PROCESSES.items():
        pid = proc_info["pid"] if isinstance(proc_info, dict) else proc_info
        if psutil.pid_exists(pid):
            running.append(f"{os.path.basename(path)} (PID {pid})")
        else:
            not_running.append(f"{os.path.basename(path)} (PID {pid}) [not running]")
    result = ""
    if running:
        result += "Running background processes:\n" + "\n".join(running)
    if not_running:
        if result:
            result += "\n"
        result += "Tracked but not running:\n" + "\n".join(not_running)
    if not result:
        result = "No background processes tracked."
    return result

def terminate_execution(filepath):
    abs_path = str(Path(os.path.expanduser(filepath)).resolve())
    proc_info = BACKGROUND_PROCESSES.get(abs_path)
    if not proc_info:
        target_name = os.path.basename(filepath)
        for stored_path, stored_info in BACKGROUND_PROCESSES.items():
            if os.path.basename(stored_path) == target_name:
                abs_path, proc_info = stored_path, stored_info
                break
    if not proc_info:
        tracked = "\n".join(BACKGROUND_PROCESSES.keys())
        return f"No background process found for {filepath}.\nTracked processes:\n{tracked or 'None'}"
    pid = proc_info["pid"] if isinstance(proc_info, dict) else proc_info
    try:
        p = psutil.Process(pid)
        for child in p.children(recursive=True):
            child.terminate()
        p.terminate()
        p.wait(timeout=5)
        del BACKGROUND_PROCESSES[abs_path]
        save_background_processes()
        return f"Terminated process for {filepath} (PID {pid})."
    except psutil.NoSuchProcess:
        del BACKGROUND_PROCESSES[abs_path]
        save_background_processes()
        return f"Process for {filepath} (PID {pid}) was already terminated."
    except Exception as e:
        return f"Error terminating process: {e}"
    
def image_to_ascii_color(img, width=None):
    """internal"""
    ascii_chars = "█▓▒░ "
    if width is None:
        try:
            width = shutil.get_terminal_size().columns
        except Exception:
            width = 80  # fallback
    img = img.convert("RGB")
    w, h = img.size
    aspect_ratio = h / w
    new_height = int(aspect_ratio * width * 0.55)
    img = img.resize((width, new_height))
    pixels = list(img.getdata())
    ascii_str = ""
    for i, (r, g, b) in enumerate(pixels):
        brightness = int(0.299*r + 0.587*g + 0.114*b)
        char = ascii_chars[brightness * (len(ascii_chars)-1) // 255]
        ascii_str += f"\033[38;2;{r};{g};{b}m{char}\033[0m"
        if (i + 1) % width == 0:
            ascii_str += "\n"
    return ascii_str

def image_to_ascii_grayscale(img, width=80):
    """internal"""
    unicode_blocks = "█▓▒░ "
    img = img.convert("L")
    w, h = img.size
    aspect_ratio = h / w
    new_height = int(aspect_ratio * width * 0.55)
    img = img.resize((width, new_height))
    pixels = img.getdata()
    ascii_str = ""
    for i, pixel in enumerate(pixels):
        ascii_str += unicode_blocks[pixel * len(unicode_blocks) // 256]
        if (i + 1) % width == 0:
            ascii_str += "\n"
    return ascii_str

load_background_processes()