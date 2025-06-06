import os
import re
import ast
import time
import sys
import google.generativeai as genai
from google.generativeai.types import generation_types
from functions.function_calls import create_file, edit_file, read, delete, execute, list_known_files, list_background_processes, terminate_execution
from pathlib import Path
import json
import threading
from dotenv import load_dotenv
from colorama import init, Fore, Style
init(autoreset=True)

load_dotenv()

HISTORY_FILE = "chat_history.txt"
FULL_HISTORY_FILE = "full_chat_history.txt"
PROMPT_FILE = "system_prompt_play.txt"
# Global model management
CURRENT_MODEL = "gemini-2.0-flash-thinking-exp-01-21"  # Default model
model = genai.GenerativeModel(CURRENT_MODEL)
PROMPT_FILE = "system_prompts\system_prompt_play.txt"

API_KEYS = [
    os.getenv(f"GOOGLE_API_KEY_{i+1}") for i in range(20)
]

CURRENT_API_KEY_INDEX = 0  # Add this near your API_KEYS definition

# Global variable to store last read data
last_read_data = None

# Define which functions are immediate
IMMEDIATE_FUNCTIONS = {"read", "execute", "list_known_files", "create_file", "edit_file", "delete", "list_known_processes", "terminate_process"}

PLUGIN_FUNCTIONS = {}

def reload_functions():
    load_functions()
    return "functions reloaded."

def setup_wizard():
    print(f"{Fore.GREEN}Welcome to PersonalAI setup!{Style.RESET_ALL}")
    api_key = input("Enter your Google Gemini API key: ").strip()
    if api_key:
        # Check for duplicates and find next available index
        existing_keys = []
        existing_indices = set()
        if os.path.exists(".env"):
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("GOOGLE_API_KEY_"):
                        key_val = line.strip().split("=", 1)[-1]
                        existing_keys.append(key_val)
                        idx_part = line.strip().split("=", 1)[0].split("_")[-1]
                        try:
                            existing_indices.add(int(idx_part))
                        except Exception:
                            pass
        if api_key in existing_keys:
            print(f"{Fore.YELLOW}API key already exists in .env!{Style.RESET_ALL}")
        else:
            idx = 1
            while idx in existing_indices:
                idx += 1
            with open(".env", "a", encoding="utf-8") as f:
                f.write(f"\nGOOGLE_API_KEY_{idx}={api_key}")
            print(f"{Fore.GREEN}API key saved as GOOGLE_API_KEY_{idx} in .env. You can add more keys later with /addkey.{Style.RESET_ALL}")
        # Reload environment
        load_dotenv(override=True)
    else:
        print(f"{Fore.RED}No API key entered. You can add one later with /addkey.{Style.RESET_ALL}")
    prompt = input("Enter your system prompt (or leave blank for default): ").strip()
    if prompt:
        with open("system_prompt_help.txt", "w", encoding="utf-8") as f:
            f.write(prompt)
        print(f"{Fore.GREEN}System prompt saved.{Style.RESET_ALL}")
    else:
        print(f"{Fore.YELLOW}Using default system prompt.{Style.RESET_ALL}")

def load_system_prompt():
    global PROMPT_FILE
    if os.path.exists(PROMPT_FILE):
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "No system prompt provided. "

def load_history(system_prompt, name=None):
    history = []
    history.append({"role": "user", "parts": [system_prompt]})
    history_file = get_history_file(name)
    if os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines:
                if line.startswith("User: "):
                    history.append({"role": "user", "parts": [line[6:].strip()]})
                elif line.startswith("Gemini: "):
                    history.append({"role": "model", "parts": [line[8:].strip()]})
    return history

def append_user_history(role, message, name=None):
    full_history_file = get_full_history_file(name)
    with open(full_history_file, "a", encoding="utf-8") as f:
        if role == "user":
            f.write(f"User: {message}\n\n")
        elif role == "model":
            filtered_message = remove_thinking_sections(message)
            f.write(f"Gemini: {filtered_message}\n\n")
        elif role == "system":
            f.write(f"System: {message}\n\n")
        elif role == "ascii_art":
            f.write(f"{message}\n\n")


def append_ai_history(role, message, name=None):
    history_file = get_history_file(name)
    with open(history_file, "a", encoding="utf-8") as f:
        if role == "user":
            f.write(f"User: {message}\n\n")
        elif role == "model":
            f.write(f"Gemini: {message}\n\n")
        elif role == "system":
            f.write(f"System: {message}\n\n")
        elif role == "ascii_art":
            f.write(f"{message}\n\n")


def parse_function_call(call_str):
    import re
    import ast

    call_str = call_str.strip()
    # Remove code block markers if present
    call_str = re.sub(r"^```(?:\w+)?|```$", "", call_str, flags=re.MULTILINE).strip()

    func_match = re.match(r"(\w+)\((.*)\)", call_str, re.DOTALL)
    if not func_match:
        return None, {}, []
    func_name = func_match.group(1)
    args_str = func_match.group(2)

    # Try to parse arguments using ast
    args_dict = {}
    try:
        # Build a fake function for parsing
        fake_func = f"f({args_str})"
        tree = ast.parse(fake_func, mode="eval")
        call = tree.body
        for kw in call.keywords:
            # Use ast.literal_eval for safe evaluation of values
            try:
                val = ast.literal_eval(kw.value)
            except Exception:
                # If literal_eval fails (e.g., for code blocks), fallback to string
                val = ast.get_source_segment(fake_func, kw.value)
            args_dict[kw.arg] = val
    except Exception as e:
        # fallback: try to extract at least filepath
        filepath_match = re.search(r'filepath\s*=\s*["\']([^"\']+)["\']', args_str)
        if filepath_match:
            args_dict['filepath'] = filepath_match.group(1)
        contents_match = re.search(r'contents\s*=\s*["\'](.+)["\']', args_str, re.DOTALL)
        if contents_match:
            args_dict['contents'] = contents_match.group(1)

    return func_name, args_dict, []

def handle_function_call(response_text):
    global last_read_data
    response_text = response_text.strip()
    # This part strips fences if the *entire* response is a code block
    if response_text.startswith("```") and response_text.endswith("```"):
        lines = response_text.splitlines()
        if len(lines) >= 2:
            response_text = "\n".join(lines[1:-1]).strip()

    func_name, args_dict, extras = parse_function_call(response_text)

    if func_name:
        try:
            if func_name == "read":
                path = args_dict.get("filepath") or args_dict.get("path")
                if path:
                    # Only normalize if it's not a URL
                    if not (isinstance(path, str) and (path.startswith("http://") or path.startswith("https://"))):
                        path = path.replace("\\", "/")
                        path = os.path.normpath(path)
                result = read(path)
                last_read_data = result
            elif func_name == "reload_functions":
                result = reload_functions()
                return {"text": f"[Function] {result}"}, True
            elif func_name in PLUGIN_FUNCTIONS:
                plugin_func = PLUGIN_FUNCTIONS[func_name]
                import inspect
                sig = inspect.signature(plugin_func)
                filtered_args = {k: v for k, v in args_dict.items() if k in sig.parameters}
                result = plugin_func(**filtered_args)
            else:
                return None, False

            # --- Centralized image handling for all functions ---
            if result is None or (isinstance(result, str) and not result.strip()):
                return {"text": "[Function] Function returned nothing."}, True

            if isinstance(result, dict):
                if result.get("type") == "image":
                    # Pass through all relevant fields for images
                    return {
                        "image_bytes": result.get("image_bytes"),
                        "mime_type": result.get("mime_type"),
                        "ascii_art": result.get("ascii_art"),
                        "text": result.get("text", "[Image]"),
                        "path": result.get("path"),
                    }, True
                # --- Show ascii art for image_search with content=True ---
                if result.get("type") == "image_search" and result.get("ascii_summary"):
                    return {
                        "text": result["ascii_summary"],
                        "ai_text": result["ascii_summary"],
                        "images": result.get("images"),
                    }, True
                elif result.get("type") == "text" and "content" in result:
                    content = result["content"]
                    if not content.strip():
                        return {"text": "[File is empty]"}, True
                    return {"text": content}, True
                elif result.get("type") == "directory" and "items" in result:
                    dir_listing = "\n".join(result["items"])
                    if not dir_listing.strip():
                        return {"text": "[Directory is empty]"}, True
                    return {"text": dir_listing}, True
                elif result.get("type") == "binary" and "sample" in result:
                    print(f"\n--- Binary File Sample: {result.get('path', 'Unknown path')} ---")
                    print(f"(First 128 bytes, Base64 encoded)")
                    print("--- End Binary File Sample ---\n")
                    return {"text": result["sample"]}, True
                elif result.get("type") == "webpage":
                    summary = result.get("summary", "")
                    full_content = result.get("text", "")
                    ai_text = result.get("ai_text", full_content)
                    url = args_dict.get("url") or args_dict.get("filepath") or args_dict.get("path") or ""
                    return {
                        "text": summary,
                        "ai_text": ai_text,
                        "url": url
                    }, True
                elif result.get("type") == "image_search":
                    images = result.get("images", [])
                    msg = result.get("text", "")
                    ai_msg = result.get("ai_text", msg)
                    # If no text, but images exist, create a summary for the user
                    if not msg and images:
                        msg = "\n".join(
                            f"Image: {img.get('text', '[Image]')}URL: {img.get('path') or '[no url]'}"
                            for img in images
                        )
                    # If images have image_bytes, prepare gemini_input as pairs: text (url), image
                    gemini_input = []
                    for img in images:
                        url = img.get("path") or img.get("url") or "[no url]"
                        desc = img.get("text") or "[Image]"
                        text_part = f"Image from URL: {url}\nDescription: {desc}"
                        gemini_input.append(text_part)
                        if img.get("image_bytes") and img.get("mime_type"):
                            gemini_input.append({
                                "mime_type": img["mime_type"],
                                "data": img["image_bytes"]
                            })
                    return {
                        "text": msg,
                        "ai_text": ai_msg if ai_msg else msg,
                        "images": images,
                        "gemini_input": gemini_input if gemini_input else None
                    }, True
            # Fallback for other return types
            return {"text": f"[Function] {result}"}, True

        except Exception as e:
            print(f"[ERROR] Exception during function execution ({func_name}): {e}")
            import traceback
            traceback.print_exc()
            return {"text": f"[Function] Error executing function call '{func_name}': {e}"}, True
    return None, False

def extract_function_calls(response_text):
    # Remove code block markers if present
    response_text = re.sub(r"^```(?:\w+)?|```$", "", response_text.strip(), flags=re.MULTILINE)
    known_funcs = get_known_functions()
    pattern = re.compile(rf"({'|'.join(map(re.escape, known_funcs))})\s*\((.*?)\)", re.DOTALL)
    calls = []
    # Find all matches and ensure parentheses are balanced for each
    for match in pattern.finditer(response_text):
        start = match.start()
        func_call = match.group(0)
        # Check for balanced parentheses from the match start
        open_parens = 0
        for i, c in enumerate(response_text[start:]):
            if c == '(':
                open_parens += 1
            elif c == ')':
                open_parens -= 1
                if open_parens == 0:
                    func_call = response_text[start:start+i+1]
                    calls.append(func_call)
                    break
    return calls

def try_send_message(chat, message, stream=False):
    global CURRENT_API_KEY_INDEX
    import re
    import time
    
    total_keys = len([key for key in API_KEYS if key])  # Count non-None keys
    failed_keys_with_time = {}  # Track failures with timestamps: {idx: (timestamp, retry_delay)}
    
    attempts = 0
    max_attempts = total_keys * 2  # Give each key a second chance
    
    while attempts < max_attempts:
        idx = CURRENT_API_KEY_INDEX % total_keys
        key = API_KEYS[idx]
        
        if not key:
            CURRENT_API_KEY_INDEX = (CURRENT_API_KEY_INDEX + 1) % total_keys
            attempts += 1
            continue
        
        # Check if this key failed recently
        current_time = time.time()
        if idx in failed_keys_with_time:
            fail_time, retry_delay = failed_keys_with_time[idx]
            if current_time < fail_time + retry_delay:
                # Still too early to retry this key
                CURRENT_API_KEY_INDEX = (CURRENT_API_KEY_INDEX + 1) % total_keys
                attempts += 1
                continue
            else:
                # Enough time has passed, remove from failed list
                del failed_keys_with_time[idx]
        
        try:
            genai.configure(api_key=key)
            if stream:
                return chat.send_message(message, stream=True)
            else:
                return chat.send_message(message)
                
        except Exception as e:
            error_str = str(e)
            
            # Extract retry delay from error if present
            retry_delay = 60  # Default delay
            delay_match = re.search(r'seconds:\s*(\d+)', error_str)
            if delay_match:
                retry_delay = int(delay_match.group(1))
            
            # Simplify error messages and mark key as temporarily failed
            if "429" in error_str and "quota" in error_str.lower():
                print(f"{Fore.RED}[ERROR] API key {idx+1}: Quota exceeded. Will retry in {retry_delay}s{Style.RESET_ALL}")
                failed_keys_with_time[idx] = (current_time, retry_delay)
                
            elif "rate limit" in error_str.lower():
                print(f"{Fore.RED}[ERROR] API key {idx+1}: Rate limited. Will retry in {retry_delay}s{Style.RESET_ALL}")
                failed_keys_with_time[idx] = (current_time, retry_delay)
                
            else:
                short_error = error_str[:100] + "..." if len(error_str) > 100 else error_str
                print(f"{Fore.RED}[ERROR] API key {idx+1}: {short_error}{Style.RESET_ALL}")
                failed_keys_with_time[idx] = (current_time, 30)  # Short retry for unknown errors
            
            CURRENT_API_KEY_INDEX = (CURRENT_API_KEY_INDEX + 1) % total_keys
            attempts += 1
            
            # If all keys are temporarily failed, wait for the soonest one to become available
            if len(failed_keys_with_time) >= total_keys:
                # Find the key that will be available soonest
                soonest_available = min(
                    (fail_time + retry_delay - current_time for fail_time, retry_delay in failed_keys_with_time.values()),
                    default=0
                )
                if soonest_available > 0:
                    print(f"{Fore.YELLOW}[SYSTEM] All keys temporarily failed. Waiting {int(soonest_available)}s for next retry...{Style.RESET_ALL}")
                    time.sleep(soonest_available + 1)  # Add 1 second buffer
                    # Clear expired failures
                    current_time = time.time()
                    failed_keys_with_time = {
                        idx: (fail_time, retry_delay) 
                        for idx, (fail_time, retry_delay) in failed_keys_with_time.items()
                        if current_time < fail_time + retry_delay
                    }
    
    # If we get here, all attempts failed
    raise Exception(f"All API keys failed after {max_attempts} attempts. Please check your keys and quotas.")

def load_functions():
    import importlib.util
    import glob

    PLUGIN_FUNCTIONS.clear()

    plugin_folder = Path("functions")
    plugin_folder.mkdir(exist_ok=True)
    builtins = {
        "create_file", "edit_file", "read", "delete", "execute", "list_known_files",
        "list_background_processes", "terminate_execution"
    }
    for file in glob.glob(str(plugin_folder / "*.py")):
        module_name = Path
        module_name = Path(file).stem
        spec = importlib.util.spec_from_file_location(module_name, file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for attr in dir(module):
            if (
                callable(getattr(module, attr))
                and not attr.startswith("_")
                and attr not in builtins
            ):
                PLUGIN_FUNCTIONS[attr] = getattr(module, attr)
    register_builtin_functions()
    update_function_calls_with_functions()

def register_builtin_functions():
    PLUGIN_FUNCTIONS.update({
        "create_file": create_file,
        "edit_file": edit_file,
        "read": read,
        "delete": delete,
        "execute": execute,
        "list_known_files": list_known_files,
        "list_background_processes": list_background_processes,
        "terminate_execution": terminate_execution,
        "reload_functions": reload_functions,
    })

def get_known_functions():
    builtins = [
        "create_file", "edit_file", "read", "delete", "execute", "list_known_files",
        "list_background_processes", "terminate_execution", "reload_functions"  # <-- add here
    ]
    return builtins + list(PLUGIN_FUNCTIONS.keys())

def load_function_calls_guide():
    guide_path = Path("general_function_calls.txt")
    if guide_path.exists():
        with guide_path.open("r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def load_function_calls_signatures():
    guide_path = Path("function_calls.txt")
    if guide_path.exists():
        with guide_path.open("r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def start_plugin_watcher():
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        class PluginChangeHandler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory:
                    return
                load_functions()

        plugin_folder = Path("functions")
        plugin_folder.mkdir(exist_ok=True)
        event_handler = PluginChangeHandler()
        observer = Observer()
        observer.schedule(event_handler, str(plugin_folder), recursive=False)
        observer.daemon = True
        observer.start()
    except ImportError:
        print("[WARNING] watchdog not installed, automatic plugin reload disabled.", file=sys.stderr)

def update_function_calls_with_functions():
    guide_path = Path("function_calls.txt")
    plugin_info = get_plugin_functions_info()
    lines = ["// Auto-generated plugin function signatures\n"]
    # Add built-in signatures
    lines.append("reload_functions()\n")  # <-- add this line
    for func_name, doc, args in plugin_info:
        arg_str = ", ".join(args)
        lines.append(f"{func_name}({arg_str})\n")
    with guide_path.open("w", encoding="utf-8") as f:
        f.writelines(lines)

def get_plugin_functions_info():
    import ast
    plugin_folder = Path("functions")
    plugin_info = []
    for file in plugin_folder.glob("*.py"):
        with open(file, "r", encoding="utf-8") as f:
            source = f.read()
        try:
            tree = ast.parse(source)
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    func_name = node.name
                    # Skip private/support functions
                    if func_name.startswith("_"):
                        continue
                    docstring = ast.get_docstring(node) or ""
                    if "internal" in docstring.lower() or "support" in docstring.lower():
                        continue
                    args = []
                    defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + node.args.defaults
                    for arg, default in zip(node.args.args, defaults):
                        if default is not None:
                            try:
                                default_val = ast.literal_eval(default)
                            except Exception:
                                default_val = None
                            if default_val is None:
                                placeholder = '"Your {}"'.format(arg.arg.capitalize())
                                args.append(f'{arg.arg}={placeholder}')
                            elif isinstance(default_val, str):
                                args.append(f'{arg.arg}="{default_val}"')
                            else:
                                args.append(f'{arg.arg}={default_val}')
                        else:
                            placeholder = '"Your {}"'.format(arg.arg.capitalize())
                            args.append(f'{arg.arg}={placeholder}')
                    plugin_info.append((func_name, docstring.strip(), args))
        except Exception as e:
            print(f"[PLUGIN SCAN ERROR] {file}: {e}")
    return plugin_info

def pop_background_errors():
    errors_path = Path("background_errors.json")
    if not errors_path.exists():
        return ""
    try:
        with errors_path.open("r", encoding="utf-8") as f:
            errors = json.load(f)
        if not errors:
            return ""
        # Format errors for display
        formatted = "[BACKGROUND PROCESS ERRORS]\n"
        for err in errors:
            formatted += f"File: {err.get('file', 'Unknown')}\nError: {err.get('error', '')}\nTime: {err.get('time', '')}\n\n"
        # Clear the file
        with errors_path.open("w", encoding="utf-8") as f:
            f.write("[]")
        return formatted.strip()
    except Exception as e:
        return f"[Error reading background_errors.json: {e}]"

def save_summary(summary):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        f.write(f"[PREVIOUS SUMMARY]\n{summary}\n[END SUMMARY]\n")

def save_summary_and_memory(summary, memory):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        f.write(f"[PREVIOUS SUMMARY]\n{summary}\n[END SUMMARY]\n")
        f.write(f"[MEMORY]\n{memory}\n[END MEMORY]\n")

def load_summary(name=None):
    history_file = get_history_file(name)
    if os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return data.get("summary", [])
            except Exception:
                pass
    return []

def load_memory(name=None):
    history_file = get_history_file(name)
    if os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return data.get("memory", [])
            except Exception:
                pass
    return []

def maybe_summarize_history(chat, history, name=None):
    import google.generativeai as genai
    import json
    import re

    prev_summary = load_summary(name)
    prev_memory = load_memory(name)

    SUMMARIZER_SYSTEM_PROMPT = (
        "You are an advanced conversation summarizer and memory manager for a personal AI assistant.\n\n"
        f"[PREVIOUS SUMMARY]\n{json.dumps(prev_summary, ensure_ascii=False, indent=2)}\n[END SUMMARY]\n"
        f"[MEMORY]\n{json.dumps(prev_memory, ensure_ascii=False, indent=2)}\n[END MEMORY]\n\n"
        
        "CRITICAL INSTRUCTIONS:\n"
        "• PRESERVE all previous summary and memory items EXACTLY - do not modify, rewrite, or remove them\n"
        "• ONLY ADD new information from the current conversation session\n"
        "• Write summary and memory in the SAME LANGUAGE as the conversation (auto-detect from user/AI messages)\n"
        "• If conversation is in Ukrainian, write in Ukrainian. If English, write in English, etc.\n"
        "• Summarize EVERYTHING regardless of content type (technical, personal, creative, etc.)\n"
        "• Focus heavily on the MOST RECENT exchanges - they are most important for context restoration\n\n"
        
        "SUMMARY GUIDELINES:\n"
        "• Write chronological bullet points of key events and exchanges\n"
        "• Quote or closely paraphrase important statements, especially recent ones\n"
        "• Include specific details: file paths, URLs, commands, technical terms, names\n"
        "• Mention function calls and their results (file operations, web searches, etc.)\n"
        "• Write from AI's first-person perspective ('I did X') and user's third-person ('User asked Y')\n"
        "• Preserve the tone and style of the original conversation\n\n"
        
        "MEMORY GUIDELINES:\n"
        "• Store important facts, preferences, goals, and ongoing projects\n"
        "• Include technical details: file locations, preferred tools, configuration settings\n"
        "• Remember user's working style, communication preferences, and context\n"
        "• Update existing memory items if new information contradicts or extends them\n"
        "• Keep specific URLs, file paths, and identifiers that user references frequently\n\n"
        
        "SPECIAL HANDLING:\n"
        "• '[System Message]:' entries are function call results, not user messages\n"
        "• When user works with files, remember the file paths and purposes\n"
        "• When user searches web/images/videos, note the queries and any important results\n"
        "• If user modifies code or configurations, remember what was changed and why\n"
        "• Track ongoing tasks, incomplete requests, or things user wants to revisit\n\n"
        
        "OUTPUT FORMAT:\n"
        "Return ONLY valid JSON (no markdown, no code blocks, no extra text):\n"
        "{\n"
        '  "language": "detected_language_code",\n'
        '  "summary": [\n'
        '    "Chronological bullet point 1",\n'
        '    "Detailed bullet point 2 with specific info",\n'
        '    "Recent exchange with quotes or paraphrasing"\n'
        '  ],\n'
        '  "memory": [\n'
        '    "Important fact or preference",\n'
        '    "Technical detail or configuration",\n'
        '    "Ongoing project or goal"\n'
        '  ]\n'
        "}\n\n"
        
        "EXAMPLE (Ukrainian conversation):\n"
        "{\n"
        '  "language": "ukrainian",\n'
        '  "summary": [\n'
        '    "Користувач запитав про налаштування VS Code для Python",\n'
        '    "Я допомогла створити файл settings.json з шляхом: C:/Users/denis/AppData/Roaming/Code/User/settings.json",\n'
        '    "Користувач протестував автодоповнення та попросив додати конфігурацію для лінтера",\n'
        '    "Я оновила налаштування, додавши pylint конфігурацію"\n'
        '  ],\n'
        '  "memory": [\n'
        '    "Користувач використовує VS Code для Python розробки",\n'
        '    "Потрібна конфігурація: автодоповнення, форматування, лінтинг",\n'
        '    "Шлях до налаштувань: C:/Users/denis/AppData/Roaming/Code/User/settings.json"\n'
        '  ]\n'
        "}\n\n"
        
        "Remember: Your job is to help the AI remember context accurately after a restart. Be precise, detailed, and comprehensive."
    )

    def extract_json(text):
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return match.group(0)
        return text

    summary_history = [{"role": "user", "parts": [SUMMARIZER_SYSTEM_PROMPT]}]
    for entry in history[1:]:
        parts = []
        for part in getattr(entry, "parts", []):
            parts.append(part)
        if parts:
            summary_history.append({"role": getattr(entry, "role", None), "parts": parts})
    summary_chat = model.start_chat(history=summary_history)
    summary_prompt = "Summarize the above conversation."
    summary_response = try_send_message(summary_chat, summary_prompt)
    new_summary_raw = summary_response.text.strip()

    # Try to parse JSON
    try:
        raw_json = extract_json(new_summary_raw)
        summary_json = json.loads(raw_json)

        # Extract language if present
        language = summary_json.get("launguage") or summary_json.get("language")
        if language:
            print(f"[SYSTEM] Detected summary language: {language}")

        # Merge with previous summary/memory (they are already lists)
        merged_summary = merge_lists(prev_summary, summary_json.get("summary", []))
        merged_memory = merge_lists(prev_memory, summary_json.get("memory", []))

        # Save pretty-printed JSON to chat_history.txt
        with open(get_history_file(name), "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "summary": merged_summary,
                "memory": merged_memory,
                "language": language  # Optionally save language in history
            }, indent=2, ensure_ascii=False))
        print("[SYSTEM] Conversation summary and memory saved as valid JSON (in text file).")
    except Exception as e:
        print(f"[SYSTEM] Failed to parse JSON summary: {e}")
        # Fallback: Save raw text in summary block
        with open(get_history_file(name), "w", encoding="utf-8") as f:
            f.write(new_summary_raw)
        print("[SYSTEM] Saved raw summary as fallback.")

def check_and_summarize_if_needed(chat, history):
    TOKEN_LIMIT = 1000000
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > TOKEN_LIMIT:
            print("[SYSTEM] Chat history exceeded token limit, summarizing...")
            maybe_summarize_history(chat, history)

def main():
    # --- Conversation auto-selection logic ---
    conversations_dir = "conversations"
    most_recent = None
    if os.path.exists(conversations_dir):
        subdirs = [os.path.join(conversations_dir, d) for d in os.listdir(conversations_dir) if os.path.isdir(os.path.join(conversations_dir, d))]
        if subdirs:
            # Find most recently USED conversation by checking full_chat_history.txt modification time
            most_recent_time = 0
            for subdir in subdirs:
                history_file = os.path.join(subdir, "full_chat_history.txt")
                if os.path.exists(history_file):
                    mtime = os.path.getmtime(history_file)
                    if mtime > most_recent_time:
                        most_recent_time = mtime
                        most_recent = subdir
            
            # If no history files found, fall back to folder modification time
            if not most_recent:
                most_recent = max(subdirs, key=os.path.getmtime)
            
            most_recent_name = os.path.basename(most_recent)
            set_conversation(most_recent_name)
        else:
            create_conversation("default")
    else:
        os.makedirs(conversations_dir, exist_ok=True)
        create_conversation("default")
    api_key = os.getenv("GOOGLE_API_KEY_1")
    if not api_key:
        print("Error: Please set the GOOGLE_API_KEY_1 environment variable with your Google AI Studio API key.")
        return

    genai.configure(api_key=api_key)
    system_prompt = load_system_prompt()
    summary = load_summary()
    memory = load_memory()
    if summary:
        if isinstance(summary, list):
            summary_str = "\n".join(summary)
        else:
            summary_str = str(summary)
        system_prompt += "\n\n[CONVERSATION SUMMARY]\n" + summary_str
    if memory:
        if isinstance(memory, list):
            memory_str = "\n".join(memory)
        else:
            memory_str = str(memory)
        system_prompt += "\n\n[MEMORY]\n" + memory_str
    function_calls_guide = load_function_calls_guide()
    function_calls_signatures = load_function_calls_signatures()

    combined_prompt = system_prompt
    if function_calls_guide:
        combined_prompt += "\n\n[FUNCTION CALLS GUIDE]\n" + function_calls_guide
    if function_calls_signatures:
        combined_prompt += "\n\n[FUNCTION CALLS]\n" + function_calls_signatures

    history = load_history(combined_prompt)

    try:
        known_files = list_known_files()
        if known_files:
            files_context = "[Known Files]\n" + "\n".join(known_files)
            history.append({"role": "user", "parts": [files_context]})
    except Exception as e:
        print(f"[SYSTEM] Could not load known files: {e}")

    chat = model.start_chat(history=history)

    print("Chat with Gemini (type '/exit', '/help' for all commands)\n")
    if os.path.exists(FULL_HISTORY_FILE):
        with open(FULL_HISTORY_FILE, "r", encoding="utf-8") as f:
            block = []
            for line in f:
                if line.strip() == "":
                    if block:
                        block_text = "\n".join(block)
                        if block_text.startswith("Gemini:"):
                            print(f"{Fore.YELLOW}Gemini:{Style.RESET_ALL}{block_text[len('Gemini:'):]}")
                        elif block_text.startswith("User:"):
                            print(f"{Fore.CYAN}You:{Style.RESET_ALL}{block_text[len('User:'):]}")
                        elif block_text.startswith("System:"):
                            print(f"{Fore.GREEN}System:{Style.RESET_ALL}{block_text[len('System:'):]}")
                        else:
                            # This is ASCII art (no prefix)
                            print(block_text)
                        print()
                        block = []
                else:
                    block.append(line.rstrip("\n"))
            if block:
                block_text = "\n".join(block)
                if block_text.startswith("Gemini:"):
                    print(f"{Fore.YELLOW}Gemini:{Style.RESET_ALL}{block_text[len('Gemini:'):]}")
                elif block_text.startswith("User:"):
                    print(f"{Fore.CYAN}You:{Style.RESET_ALL}{block_text[len('User:'):]}")
                elif block_text.startswith("System:"):
                    print(f"{Fore.GREEN}System:{Style.RESET_ALL}{block_text[len('System:'):]}")
                else:
                    # This is ASCII art (no prefix)
                    print(block_text)
                print()
    system_msg = None
    message_counter = 4

    try:
        while True:
            # 1. Get user input if no pending system message
            if not system_msg:
                user_input = input(f"{Fore.CYAN}You:{Style.RESET_ALL} ").strip()
                if not user_input:
                    continue

                # Handle commands (exit, help, addkey, etc.)
                if user_input.lower() in ("/exit", "/quit"):
                    print("\nSummarizing conversation before exit...\n")
                    maybe_summarize_history(chat, chat.history)
                    print("Goodbye!\n")
                    break
                elif user_input.lower() in ("/plugins", "/functions"):
                    print("\nFunctions available (from function_calls.txt):\n")
                    try:
                        with open("function_calls.txt", "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line and not line.startswith("//"):
                                    print(f" - {line}")
                    except Exception as e:
                        print(f"\nCould not read function_calls.txt: {e}\n")
                    continue
                if user_input.lower() == "/relaunch":
                    print("\nRefreshing AI state with current conversation history...\n")
                    try:
                        current_history = list(chat.history)
                        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                            is_system_prompt_first = (
                                current_history and
                                current_history[0].role == "user" and
                                hasattr(current_history[0].parts[0], 'text') and
                                current_history[0].parts[0].text == system_prompt
                            )
                            start_index = 1 if is_system_prompt_first else 0
                            for entry in current_history[start_index:]:
                                role = entry.role
                                message = "".join(part.text for part in entry.parts if hasattr(part, 'text'))
                                if role == "user":
                                    f.write(f"User: {message}\n")
                                elif role == "model":
                                    if not message.startswith("[FUNCTION RESULT"):
                                        f.write(f"Gemini: {message}\n")
                        chat = model.start_chat(history=current_history)
                        print("AI state refreshed.\n")
                        continue
                    except Exception as e:
                        print(f"\n[ERROR] Failed to refresh AI state: {e}\n")
                        continue

                if user_input.lower().startswith("reload functions"):
                    load_functions()
                    print("\nFunctions reloaded.\n")
                    continue

                if user_input.lower() == "/clear":
                    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                        pass
                    with open(FULL_HISTORY_FILE, "w", encoding="utf-8") as f:
                        pass
                    known_files_path = "known_files.txt"
                    if os.path.exists(known_files_path):
                        with open(known_files_path, "w", encoding="utf-8") as f:
                            pass
                    # Stop all background processes
                    try:
                        from pathlib import Path
                        import json
                        import psutil
                        bg_file = Path("background_processes.json")
                        if bg_file.exists():
                            with bg_file.open("r", encoding="utf-8") as f:
                                bg_data = json.load(f)
                            for proc_info in bg_data.values():
                                pid = proc_info.get("pid")
                                if pid:
                                    try:
                                        p = psutil.Process(pid)
                                        p.terminate()
                                        print(f"{Fore.GREEN}Terminated background process PID {pid}{Style.RESET_ALL}")
                                    except Exception as e:
                                        print(f"{Fore.RED}Could not terminate PID {pid}: {e}{Style.RESET_ALL}")
                            with bg_file.open("w", encoding="utf-8") as f:
                                f.write("{}")
                    except Exception as e:
                        print(f"{Fore.RED}Failed to stop background processes: {e}{Style.RESET_ALL}")

                    os.system('cls' if os.name == 'nt' else 'clear')
                    print("Chat with Gemini (type '/exit', '/help' for all commands)\n")
                    system_prompt = load_system_prompt()
                    summary = load_summary()
                    memory = load_memory()
                    if summary:
                        system_prompt += "\n\n[CONVERSATION SUMMARY]\n" + summary
                    if memory:
                        system_prompt += "\n\n[MEMORY]\n" + memory
                    function_calls_guide = load_function_calls_guide()
                    function_calls_signatures = load_function_calls_signatures()
                    combined_prompt = system_prompt
                    if function_calls_guide:
                        combined_prompt += "\n\n[FUNCTION CALLS GUIDE]\n" + function_calls_guide
                    if function_calls_signatures:
                        combined_prompt += "\n\n[FUNCTION CALLS]\n" + function_calls_signatures
                    history = load_history(combined_prompt)
                    chat = model.start_chat(history=history)
                    continue

                if user_input.lower().startswith("/addkey"):
                    new_key = input("\nEnter new API key: ").strip()
                    existing_keys = []
                    if os.path.exists(".env"):
                        with open(".env", "r", encoding="utf-8") as f:
                            for line in f:
                                if line.startswith("GOOGLE_API_KEY_"):
                                    existing_keys.append(line.strip().split("=", 1)[-1])
                    if new_key in existing_keys:
                        print(f"{Fore.YELLOW}API key already exists in .env!{Style.RESET_ALL}")
                        continue
                    idx = 1
                    while f"GOOGLE_API_KEY_{idx}" in [f"GOOGLE_API_KEY_{i+1}" for i in range(len(existing_keys))]:
                        idx += 1
                    with open(".env", "a", encoding="utf-8") as f:
                        f.write(f"\nGOOGLE_API_KEY_{idx}={new_key}")
                    load_dotenv(override=True)
                    API_KEYS[:] = [
                        os.getenv(f"GOOGLE_API_KEY_{i+1}") for i in range(20)
                    ]
                    print(f"{Fore.GREEN}API key added as GOOGLE_API_KEY_{idx}.{Style.RESET_ALL}\n")
                    continue
                elif user_input.lower() =="/listkeys":
                    print("\nLoaded API keys:\n")
                    for idx, key in enumerate(API_KEYS, 1):
                        if key:
                            print(f"Key {idx}: {key[:6]}...{key[-4:]}")
                        else:
                            print(f"Key {idx}: [empty]")
                    print()
                    continue
                elif user_input.lower() =="/setprompt":
                    new_prompt = input("\nEnter new system prompt: ").strip()                    
                    with open("system_prompt_help.txt", "w", encoding="utf-8") as f:
                        f.write(new_prompt)
                    print(f"{Fore.GREEN}System prompt updated.{Style.RESET_ALL}\n")
                    continue
                elif user_input.lower() =="/showprompt":
                    global PROMPT_FILE
                    print(f"\n{Fore.CYAN}Current system prompt:{Style.RESET_ALL}\n")
                    print(load_system_prompt())
                    print(f"\n[Prompt file: {PROMPT_FILE}]\n")
                    continue
                elif user_input.lower().startswith("/new"):
                    parts = user_input.split(maxsplit=1)
                    if len(parts) == 2:
                        name = parts[1].strip()
                        # Summarize current conversation before switching
                        try:
                            maybe_summarize_history(chat, chat.history, current_conversation)
                        except Exception as e:
                            print(f"[SYSTEM] Failed to summarize previous conversation: {e}")
                        create_conversation(name)
                        print(f"Started new conversation: {name}\n")
                        os.system('cls' if os.name == 'nt' else 'clear')
                        print(f"Switched to conversation: {name}\n")
                        system_prompt = load_system_prompt()
                        summary = load_summary()
                        memory = load_memory()
                        if summary:
                            if isinstance(summary, list):
                                summary_str = "\n".join(summary)
                            else:
                                summary_str = str(summary)
                            system_prompt += "\n\n[CONVERSATION SUMMARY]\n" + summary_str
                        if memory:
                            if isinstance(memory, list):
                                memory_str = "\n".join(memory)
                            else:
                                memory_str = str(memory)
                            system_prompt += "\n\n[MEMORY]\n" + memory_str
                        function_calls_guide = load_function_calls_guide()
                        function_calls_signatures = load_function_calls_signatures()
                        combined_prompt = system_prompt
                        if function_calls_guide:
                            combined_prompt += "\n\n[FUNCTION CALLS GUIDE]\n" + function_calls_guide
                        if function_calls_signatures:
                            combined_prompt += "\n\n[FUNCTION CALLS]\n" + function_calls_signatures
                        history = load_history(combined_prompt)
                        chat = model.start_chat(history=history)
                        continue
                    else:
                        print("\nUsage: /new <conversation_name>\n")
                        continue
                elif user_input.lower().startswith("/delete"):
                    parts = user_input.split(maxsplit=1)
                    if len(parts) == 2:
                        name = parts[1].strip()
                        conv_dir = get_conversation_dir(name)
                        if os.path.exists(conv_dir):
                            import shutil
                            
                            # Check if we're deleting the current conversation
                            if name == current_conversation:
                                # Get all conversations except the one being deleted
                                all_conversations = list_conversations()
                                remaining_conversations = [conv for conv in all_conversations if conv != name]
                                
                                if remaining_conversations:
                                    # Switch to the most recently used conversation
                                    most_recent = None
                                    most_recent_time = 0
                                    for conv in remaining_conversations:
                                        history_file = os.path.join(get_conversation_dir(conv), "full_chat_history.txt")
                                        if os.path.exists(history_file):
                                            mtime = os.path.getmtime(history_file)
                                            if mtime > most_recent_time:
                                                most_recent_time = mtime
                                                most_recent = conv
                                    
                                    # If no history files found, use the first available conversation
                                    if not most_recent:
                                        most_recent = remaining_conversations[0]
                                    
                                    # Delete the conversation
                                    shutil.rmtree(conv_dir)
                                    print(f"\nConversation '{name}' deleted.")
                                    
                                    # Switch to the selected conversation
                                    set_conversation(most_recent)
                                    print(f"Switched to conversation: '{most_recent}'")
                                    
                                    # Clear screen and reload conversation
                                    os.system('cls' if os.name == 'nt' else 'clear')
                                    print(f"Switched to conversation: {most_recent}\n")
                                    
                                    # Print the conversation history
                                    full_history_file = get_full_history_file(most_recent)
                                    if os.path.exists(full_history_file):
                                        with open(full_history_file, "r", encoding="utf-8") as f:
                                            block = []
                                            for line in f:
                                                if line.strip() == "":
                                                    if block:
                                                        block_text = "\n".join(block)
                                                        if block_text.startswith("Gemini:"):
                                                            print(f"{Fore.YELLOW}Gemini:{Style.RESET_ALL}{block_text[len('Gemini:'):]}")
                                                        elif block_text.startswith("User:"):
                                                            print(f"{Fore.CYAN}You:{Style.RESET_ALL}{block_text[len('User:'):]}")
                                                        elif block_text.startswith("System:"):
                                                            print(f"{Fore.GREEN}System:{Style.RESET_ALL}{block_text[len('System:'):]}")
                                                        else:
                                                            print(block_text)
                                                        print()
                                                        block = []
                                                else:
                                                    block.append(line.rstrip("\n"))
                                            if block:
                                                block_text = "\n".join(block)
                                                if block_text.startswith("Gemini:"):
                                                    print(f"{Fore.YELLOW}Gemini:{Style.RESET_ALL}{block_text[len('Gemini:'):]}")
                                                elif block_text.startswith("User:"):
                                                    print(f"{Fore.CYAN}You:{Style.RESET_ALL}{block_text[len('User:'):]}")
                                                elif block_text.startswith("System:"):
                                                    print(f"{Fore.GREEN}System:{Style.RESET_ALL}{block_text[len('System:'):]}")
                                                else:
                                                    print(block_text)
                                                print()
                                    
                                    # Rebuild chat for the new conversation
                                    system_prompt = load_system_prompt()
                                    summary = load_summary(most_recent)
                                    memory = load_memory(most_recent)
                                    if summary:
                                        if isinstance(summary, list):
                                            summary_str = "\n".join(summary)
                                        else:
                                            summary_str = str(summary)
                                        system_prompt += "\n\n[CONVERSATION SUMMARY]\n" + summary_str
                                    if memory:
                                        if isinstance(memory, list):
                                            memory_str = "\n".join(memory)
                                        else:
                                            memory_str = str(memory)
                                        system_prompt += "\n\n[MEMORY]\n" + memory_str
                                    function_calls_guide = load_function_calls_guide()
                                    function_calls_signatures = load_function_calls_signatures()
                                    combined_prompt = system_prompt
                                    if function_calls_guide:
                                        combined_prompt += "\n\n[FUNCTION CALLS GUIDE]\n" + function_calls_guide
                                    if function_calls_signatures:
                                        combined_prompt += "\n\n[FUNCTION CALLS]\n" + function_calls_signatures
                                    history = load_history(combined_prompt, most_recent)
                                    chat = model.start_chat(history=history)
                                    
                                else:
                                    # No other conversations exist, prompt for new name
                                    print(f"\nConversation '{name}' is the only conversation.")
                                    new_name = input("\nEnter name for new conversation: ").strip()
                                    if not new_name:
                                        new_name = "default"
                                    
                                    # Delete the old conversation
                                    shutil.rmtree(conv_dir)
                                    print(f"Conversation '{name}' deleted.")
                                    
                                    # Create new conversation
                                    create_conversation(new_name)
                                    print(f"Created new conversation: '{new_name}'")
                                    
                                    # Clear screen and start fresh
                                    os.system('cls' if os.name == 'nt' else 'clear')
                                    print(f"Started new conversation: {new_name}\n")
                                    
                                    # Rebuild chat for the new conversation
                                    system_prompt = load_system_prompt()
                                    summary = load_summary(new_name)
                                    memory = load_memory(new_name)
                                    if summary:
                                        if isinstance(summary, list):
                                            summary_str = "\n".join(summary)
                                        else:
                                            summary_str = str(summary)
                                        system_prompt += "\n\n[CONVERSATION SUMMARY]\n" + summary_str
                                    if memory:
                                        if isinstance(memory, list):
                                            memory_str = "\n".join(memory)
                                        else:
                                            memory_str = str(memory)
                                        system_prompt += "\n\n[MEMORY]\n" + memory_str
                                    function_calls_guide = load_function_calls_guide()
                                    function_calls_signatures = load_function_calls_signatures()
                                    combined_prompt = system_prompt
                                    if function_calls_guide:
                                        combined_prompt += "\n\n[FUNCTION CALLS GUIDE]\n" + function_calls_guide
                                    if function_calls_signatures:
                                        combined_prompt += "\n\n[FUNCTION CALLS]\n" + function_calls_signatures
                                    history = load_history(combined_prompt, new_name)
                                    chat = model.start_chat(history=history)
                                
                                continue
                            else:
                                # Deleting a different conversation (not current)
                                shutil.rmtree(conv_dir)
                                print(f"\nConversation '{name}' deleted.\n")
                                continue
                        else:
                            print(f"\nConversation '{name}' not found.\n")
                            continue
                    else:
                        print("\nUsage: /delete <conversation_name>\n")
                        continue
                elif user_input.lower().startswith("/rename"):
                    parts = user_input.split(maxsplit=1)
                    if len(parts) == 2:
                        new_name = parts[1].strip()
                        if new_name == current_conversation:
                            print("\n[SYSTEM] New name is the same as the current conversation.\n")
                            continue
                        if new_name in list_conversations():
                            print(f"\n[SYSTEM] Conversation '{new_name}' already exists. Choose a different name.\n")
                            continue
                        old_dir = get_conversation_dir(current_conversation)
                        new_dir = get_conversation_dir(new_name)
                        try:
                            os.rename(old_dir, new_dir)
                            set_conversation(new_name)
                            print(f"\nConversation renamed to '{new_name}'.\n")
                            continue
                        except Exception as e:
                            print(f"\n[SYSTEM] Failed to rename conversation: {e}\n")
                            continue
                    else:
                        print("\nUsage: /rename <new_name>\n")
                        continue                
                elif user_input.lower().startswith("/promptfile"):
                    parts = user_input.split(maxsplit=1)
                    if len(parts) == 2:
                        name = parts[1].strip()
                        # Remove any prefix and extension
                        if name.lower().startswith("system_prompt_"):
                            name = name[len("system_prompt_"):]
                        if name.lower().endswith(".txt"):
                            name = name[:-4]
                        prompt_dir = "system_prompts"
                        filename = os.path.join(prompt_dir, f"system_prompt_{name}.txt")
                        if not os.path.exists(filename):
                            print(f"[SYSTEM] File '{filename}' does not exist.\n")
                            continue
                        PROMPT_FILE = filename
                        print(f"[SYSTEM] System prompt file set to: {filename}\n")
                        # Optionally reload prompt immediately
                        system_prompt = load_system_prompt()
                        continue
                    else:
                        print(f"[SYSTEM] Current prompt file: {PROMPT_FILE}")
                        print("\nUsage: /promptfile <name>\n")
                        continue
                elif user_input.lower().startswith("/saveprompt"):
                    prompt_name = input("\nEnter a name to save the current system prompt (default: help): ").strip()
                    if not prompt_name:
                        prompt_name = "help"
                    if prompt_name.lower().startswith("system_prompt_"):
                        prompt_name = prompt_name[len("system_prompt_"):]
                    if prompt_name.lower().endswith(".txt"):
                        prompt_name = prompt_name[:-4]
                    prompt_dir = "system_prompts"
                    os.makedirs(prompt_dir, exist_ok=True)
                    prompt_path = os.path.join(prompt_dir, f"system_prompt_{prompt_name}.txt")
                    # Save the current system prompt (from PROMPT_FILE) to the new file
                    current_prompt = load_system_prompt()
                    with open(prompt_path, "w", encoding="utf-8") as f:
                        f.write(current_prompt)
                    print(f"\n{Fore.GREEN}Current system prompt saved as {prompt_path}.{Style.RESET_ALL}\n")
                    continue                
                elif user_input.lower() == "/help":
                    print(f"""\n{Fore.YELLOW}Available commands:{Style.RESET_ALL}
  /addkey      - Add a new API key
  /listkeys    - List loaded API keys
  /setprompt   - Change the system prompt
  /showprompt  - Show the current system prompt
  /promptfile <filename> - Switch the system prompt file
  /model <name> - Switch to a different Gemini model
  /listmodels  - List all available Gemini models
  /clear       - Clear chat history
  /reset       - Reset all settings to default
  /help        - Show this help message
  /exit        - Quit
  /plugins     - List available plugin functions
  /new <name>  - Start a new conversation (summarizes current first)
  /load <name> - Switch to a conversation (summarizes current first)
  /list        - List all conversations
  /delete <name> - Delete a conversation (cannot delete current)
  /rename <new_name> - Rename the current conversation
""")
                    continue
                elif user_input.lower().startswith("/load"):
                    parts = user_input.split(maxsplit=1)
                    if len(parts) == 2:
                        name = parts[1].strip()
                        if name in list_conversations():
                            # Summarize current conversation before switching
                            try:
                                maybe_summarize_history(chat, chat.history, current_conversation)
                            except Exception as e:
                                print(f"\n[SYSTEM] Failed to summarize previous conversation: {e}\n")
                            set_conversation(name)
                            print(f"\nLoaded conversation: {name}\n")
                            os.system('cls' if os.name == 'nt' else 'clear')
                            print(f"\nSwitched to conversation: {name}\n")
                            # Print new conversation's history
                            full_history_file = get_full_history_file(name)
                            if os.path.exists(full_history_file):
                                with open(full_history_file, "r", encoding="utf-8") as f:
                                    block = []
                                    for line in f:
                                        if line.strip() == "":
                                            if block:
                                                block_text = "\n".join(block)
                                                if block_text.startswith("Gemini:"):
                                                    print(f"{Fore.YELLOW}Gemini:{Style.RESET_ALL}{block_text[len('Gemini:'):]}")
                                                elif block_text.startswith("User:"):
                                                    print(f"{Fore.CYAN}You:{Style.RESET_ALL}{block_text[len('User:'):]}")
                                                elif block_text.startswith("System:"):
                                                    print(f"{Fore.GREEN}System:{Style.RESET_ALL}{block_text[len('System:'):]}")
                                                else:
                                                    print(block_text)
                                                print()
                                                block = []
                                        else:
                                            block.append(line.rstrip("\n"))
                                    if block:
                                        block_text = "\n".join(block)
                                        if block_text.startswith("Gemini:"):
                                            print(f"{Fore.YELLOW}Gemini:{Style.RESET_ALL}{block_text[len('Gemini:'):]}")
                                        elif block_text.startswith("User:"):
                                            print(f"{Fore.CYAN}You:{Style.RESET_ALL}{block_text[len('User:'):]}")
                                        elif block_text.startswith("System:"):
                                            print(f"{Fore.GREEN}System:{Style.RESET_ALL}{block_text[len('System:'):]}")
                                        else:
                                            print(block_text)
                                        print()
                            # Rebuild prompt and chat for new conversation
                            system_prompt = load_system_prompt()
                            summary = load_summary(name)
                            memory = load_memory(name)
                            if summary:
                                if isinstance(summary, list):
                                    summary_str = "\n".join(summary)
                                else:
                                    summary_str = str(summary)
                                system_prompt += "\n\n[CONVERSATION SUMMARY]\n" + summary_str
                            if memory:
                                if isinstance(memory, list):
                                    memory_str = "\n".join(memory)
                                else:
                                    memory_str = str(memory)
                                system_prompt += "\n\n[MEMORY]\n" + memory_str
                            function_calls_guide = load_function_calls_guide()
                            function_calls_signatures = load_function_calls_signatures()
                            combined_prompt = system_prompt
                            if function_calls_guide:
                                combined_prompt += "\n\n[FUNCTION CALLS GUIDE]\n" + function_calls_guide
                            if function_calls_signatures:
                                combined_prompt += "\n\n[FUNCTION CALLS]\n" + function_calls_signatures
                            history = load_history(combined_prompt, name)
                            chat = model.start_chat(history=history)
                            continue
                        else:
                            print(f"\nConversation '{name}' not found. Use /list to see available conversations.\n")
                            continue
                    else:
                        print("\nUsage: /load <conversation_name>\n")
                        continue                
                if user_input.lower() == "/list":
                    conversations = list_conversations()
                    print("\nAvailable conversations:\n")
                    for conv in conversations:
                        if conv == current_conversation:
                            print(f" - {Fore.GREEN}{conv} (current){Style.RESET_ALL}")
                        else:
                            print(f" - {conv}")
                    print()
                    continue
                elif user_input.lower() == "/listmodels":
                    print(f"\n{Fore.YELLOW}Available Gemini models:{Style.RESET_ALL}\n")
                    models = get_available_models()
                    for i, model_info in enumerate(models, 1):
                        name = model_info['name']
                        display = model_info.get('display_name', name)
                        desc = model_info.get('description', 'No description available')
                        
                        if name == CURRENT_MODEL:
                            print(f" {i}. {Fore.GREEN}{name} (current){Style.RESET_ALL}")
                            print(f"    {display}")
                            print(f"    {desc}")
                        else:
                            print(f" {i}. {name}")
                            print(f"    {display}")
                            print(f"    {desc}")
                        print()
                    print(f"Use '/model <name>' or '/model <number>' to switch models.\n")
                    continue
                elif user_input.lower().startswith("/model"):
                    parts = user_input.split(maxsplit=1)
                    if len(parts) == 2:
                        model_input = parts[1].strip()
                        models = get_available_models()
                        
                        # Check if input is a number (model index)
                        if model_input.isdigit():
                            model_index = int(model_input) - 1
                            if 0 <= model_index < len(models):
                                new_model_name = models[model_index]['name']
                            else:
                                print(f"\n{Fore.RED}Invalid model number. Use '/listmodels' to see available models.{Style.RESET_ALL}\n")
                                continue
                        else:
                            # Check if input matches a model name
                            new_model_name = None
                            for model_info in models:
                                if model_input.lower() in model_info['name'].lower():
                                    new_model_name = model_info['name']
                                    break
                            
                            if not new_model_name:
                                print(f"\n{Fore.RED}Model '{model_input}' not found. Use '/listmodels' to see available models.{Style.RESET_ALL}\n")
                                continue
                        
                        if new_model_name == CURRENT_MODEL:
                            print(f"\n{Fore.YELLOW}Already using model: {CURRENT_MODEL}{Style.RESET_ALL}\n")
                            continue
                        
                        print(f"\n{Fore.CYAN}Switching to model: {new_model_name}...{Style.RESET_ALL}")
                        new_chat, message = switch_model(new_model_name, chat)
                        chat = new_chat
                        
                        if "Successfully" in message:
                            print(f"{Fore.GREEN}{message}{Style.RESET_ALL}\n")
                        else:
                            print(f"{Fore.RED}{message}{Style.RESET_ALL}\n")
                        continue
                    else:
                        print(f"\n{Fore.YELLOW}Current model: {CURRENT_MODEL}{Style.RESET_ALL}")
                        print("\nUsage: /model <name> or /model <number>")
                        print("Use '/listmodels' to see available models.\n")
                        continue

                append_user_history("user", user_input)
                append_ai_history("user", user_input)
                ai_input = user_input
            else:
                append_user_history("system", system_msg)
                append_ai_history("system", system_msg)
                ai_input = system_msg
                system_msg = None

            background_errors = pop_background_errors()
            message_counter += 1

            if message_counter % 5 == 0 and function_calls_guide:
                full_input = (
                    f"[SYSTEM PROMPT]\n{system_prompt}\n\n"
                    f"[FUNCTION CALLS GUIDE]\n{function_calls_guide}\n\n"
                    f"[FUNCTION CALLS]\n{function_calls_signatures}\n\n"
                    f"[USER MESSAGE]\n{ai_input}[\n/USER MESSAGE]"
                )
            else:
                full_input = (
                    f"[SYSTEM PROMPT]\n{system_prompt}\n\n"
                    f"[FUNCTION CALLS]\n{function_calls_signatures}\n\n"
                    f"[USER MESSAGE]\n{ai_input}[\n/USER MESSAGE]"
                )
            if background_errors:
                full_input += f"\n\n{background_errors}"

            # --- Main Gemini interaction loop ---
            chain_step_limit = 8  # Prevent infinite loops
            chain_step = 0
            full_input_for_chain = full_input
            last_gemini_response = None
            while True:
                for chain_step in range(chain_step_limit):
                    response = try_send_message(chat, full_input_for_chain)
                    last_gemini_response = response
                    response_text = response.text
                    function_calls = extract_function_calls(response_text)

                    append_user_history("model", response.text)
                    append_ai_history("model", response.text)
                    response_text_no_thinking = remove_thinking_sections(response.text)
                    if not response_text_no_thinking.strip():
                        print(f"{Fore.YELLOW}Gemini:{Style.RESET_ALL} [No response, AI may be waiting for user input or further instruction.]\n")
                    else:
                        print(f"\n{Fore.YELLOW}Gemini:{Style.RESET_ALL} {response_text_no_thinking}\n")

                    if not function_calls:
                        # No more function calls, break to user input
                        break

                    # Execute all function calls and collect results
                    system_msgs = []
                    processed_calls = []  # Use a list to preserve order
                    collected_gemini_input = []
                    collected_user_msgs = []
                    call_results = {}
                    for call in function_calls:
                        if call in processed_calls:
                            continue
                        processed_calls.append(call)
                        func_result, _ = handle_function_call(call)
                        call_results[call] = func_result

                        # Centralize result formatting
                        if func_result:
                            if isinstance(func_result, dict):
                                user_msg = func_result.get("text", "")
                                ai_msg = func_result.get("ai_text", user_msg)
                                # Collect gemini_input if present
                                if func_result.get("gemini_input"):
                                    if user_msg:
                                        collected_user_msgs.append(user_msg)
                                    collected_gemini_input.extend(func_result["gemini_input"])
                                elif "image_bytes" in func_result and "mime_type" in func_result:
                                    if user_msg:
                                        collected_user_msgs.append(user_msg)
                                    collected_gemini_input.append({
                                        "mime_type": func_result["mime_type"],
                                        "data": func_result["image_bytes"]
                                    })
                                elif func_result.get("ai_text"):
                                    if user_msg:
                                        collected_user_msgs.append(user_msg)
                                    system_msgs.append(func_result["ai_text"])
                                else:
                                    if user_msg:
                                        collected_user_msgs.append(user_msg)
                                        system_msgs.append(user_msg)
                            elif isinstance(func_result, str):
                                collected_user_msgs.append(func_result)
                                system_msgs.append(func_result)

                    # --- Centralized Gemini response logic ---
                    # Print all user/system messages (summary, etc.)
                    for msg, call in zip(collected_user_msgs, processed_calls):
                        func_result = call_results.get(call)
                        
                        # Print the system message with only "System:" prefix in green
                        print(f"{Fore.GREEN}System:{Style.RESET_ALL} {msg}")
                        append_user_history("system", msg)
                        append_ai_history("system", msg)
                        
                        # Then print ASCII art if available
                        if func_result and isinstance(func_result, dict):
                            ascii_art = func_result.get("ascii_art")
                            if ascii_art:
                                print(ascii_art)
                                # Save ASCII art to both histories so it is shown after relaunch
                                append_user_history("ascii_art", ascii_art)
                                append_ai_history("ascii_art", ascii_art)

                    # Decide what to send to Gemini: gemini_input (if any), else system_msgs
                    if collected_gemini_input:
                        full_input_for_chain = collected_gemini_input
                    elif system_msgs:
                        formatted_msgs = [f"[System Message]:{msg}" for msg in system_msgs]
                        full_input_for_chain = "\n".join(formatted_msgs)
                    else:
                        # No input to send, break
                        break
                # After all function call chaining steps, print/save only the final Gemini response
                break
    except KeyboardInterrupt:
        print("\nSummarizing conversation before exit...")
        maybe_summarize_history(chat, chat.history)
        print("Session ended.")

def extract_json(text):
    # Remove code block markers if present
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE)
    # Try to find the first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    return text

def merge_lists(old, new):
    merged = list(old)
    for item in new:
        if item not in merged:
            merged.append(item)
    return merged

def remove_thinking_sections(text):
    """
    Removes all <thinking>...</thinking> sections (multiline), trims whitespace,
    and removes any stray/trailing </thinking> tags.
    """
    # Remove all <thinking>...</thinking> blocks
    cleaned = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
    # Remove any stray/trailing </thinking> tags (with optional whitespace before/after)
    cleaned = re.sub(r"\s*</thinking>\s*$", "", cleaned, flags=re.IGNORECASE)
    # Remove any stray </thinking> anywhere else
    cleaned = re.sub(r"</thinking>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()
    
# --- Model Management ---
def get_available_models():
    """Get list of available Gemini models from Google's API"""
    try:
        models = []
        for model in genai.list_models():
            # Filter for Gemini models only
            if hasattr(model, 'name') and 'gemini' in model.name.lower():
                # Extract model name from full path (e.g., "models/gemini-pro" -> "gemini-pro")
                model_name = model.name.split('/')[-1] if '/' in model.name else model.name
                models.append({
                    'name': model_name,
                    'display_name': getattr(model, 'display_name', model_name),
                    'description': getattr(model, 'description', 'No description available')
                })
        return models
    except Exception as e:
        print(f"[WARNING] Could not fetch models from API: {e}")
        # Fallback to hardcoded list of known models
        return [
            None
        ]

def switch_model(new_model_name, chat):
    """Switch to a different Gemini model while preserving conversation context"""
    global model, CURRENT_MODEL
    
    try:
        # Summarize current conversation before switching
        context_preserved = False
        if chat and hasattr(chat, 'history') and chat.history and len(chat.history) > 1:
            try:
                maybe_summarize_history(chat, chat.history)
                context_preserved = True
                print(f"    Conversation summarized successfully")
            except Exception as e:
                print(f"    Note: Could not summarize conversation ({str(e)[:50]}...), will try direct transfer")
        
        # Create new model instance
        new_model = genai.GenerativeModel(new_model_name)
        
        # Start fresh with summarized context that will be loaded from files
        new_chat = None
        history_preserved = False
        
        # Try to transfer basic conversation context
        if chat and hasattr(chat, 'history') and chat.history and not context_preserved:
            try:
                # Attempt to transfer recent history - but be more careful
                current_history = []
                # Only take the last few messages to avoid overwhelming the new model
                recent_history = chat.history[-6:] if len(chat.history) > 6 else chat.history
                
                for msg in recent_history:
                    # Only transfer basic text messages, skip complex ones
                    if hasattr(msg, 'parts') and msg.parts:
                        # Check if this is a simple text message
                        if len(msg.parts) == 1 and hasattr(msg.parts[0], 'text'):
                            current_history.append(msg)
                
                if current_history:
                    new_chat = new_model.start_chat(history=current_history)
                else:
                    new_chat = new_model.start_chat()
            except Exception:
                # History transfer failed, start fresh
                new_chat = new_model.start_chat()
        else:
            # Start fresh - the summarized context will be available when chat restarts
            new_chat = new_model.start_chat()
        
        # If we still don't have a chat, something went wrong
        if new_chat is None:
            new_chat = new_model.start_chat()
        
        # Update globals
        model = new_model
        CURRENT_MODEL = new_model_name
        
        status_msg = f"Successfully switched to model: {new_model_name}"        
        return new_chat, status_msg
        
    except Exception as e:
        return chat, f"Failed to switch to model '{new_model_name}': {str(e)}"

# --- Conversation Management ---
current_conversation = "default"

def get_conversation_dir(name=None):
    if name is None:
        name = current_conversation
    return os.path.join("conversations", name)

def get_history_file(name=None):
    return os.path.join(get_conversation_dir(name), "chat_history.txt")

def get_full_history_file(name=None):
    return os.path.join(get_conversation_dir(name), "full_chat_history.txt")

def list_conversations():
    if not os.path.exists("conversations"):
        return []
    conversations = [d for d in os.listdir("conversations") if os.path.isdir(os.path.join("conversations", d))]
    return sorted(conversations, key=str.lower)

def set_conversation(name):
    global current_conversation, HISTORY_FILE, FULL_HISTORY_FILE
    current_conversation = name
    HISTORY_FILE = get_history_file()
    FULL_HISTORY_FILE = get_full_history_file()

def create_conversation(name):
    conv_dir = get_conversation_dir(name)
    os.makedirs(conv_dir, exist_ok=True)
    set_conversation(name)
    # Create empty files if not exist
    open(HISTORY_FILE, "a", encoding="utf-8").close()
    open(FULL_HISTORY_FILE, "a", encoding="utf-8").close()
    
if __name__ == "__main__":
    load_functions()
    start_plugin_watcher()
    main()