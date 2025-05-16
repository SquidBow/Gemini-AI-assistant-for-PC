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
PROMPT_FILE = "system_prompt.txt"
model = genai.GenerativeModel("gemini-2.0-flash") #gemini-2.0-flash-exp

API_KEYS = [
    os.getenv(f"GOOGLE_API_KEY_{i+1}") for i in range(20)
]

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
    if os.path.exists(PROMPT_FILE):
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "No system prompt provided. "

def load_history(system_prompt):
    history = []
    history.append({"role": "user", "parts": [system_prompt]})
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines:
                if line.startswith("User: "):
                    history.append({"role": "user", "parts": [line[6:].strip()]})
                elif line.startswith("Gemini: "):
                    history.append({"role": "model", "parts": [line[8:].strip()]})
    return history

def append_user_history(role, message):
    with open(FULL_HISTORY_FILE, "a", encoding="utf-8") as f:
        if role == "user":
            f.write(f"User: {message}\n\n")
        elif role == "model":
            filtered_message = remove_thinking_sections(message)
            f.write(f"Gemini: {filtered_message}\n\n")
        elif role == "system":
            f.write(f"System: {message}\n\n")
        # ...other roles as needed...

def append_ai_history(role, message):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        if role == "user":
            f.write(f"User: {message}\n\n")
        elif role == "model":
            f.write(f"Gemini: {message}\n\n")
        elif role == "system":
            f.write(f"System: {message}\n\n")

def parse_function_call(call_str):
    import re
    import ast

    # Extract function name and arguments
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
        print(f"[DEBUG] parse_function_call AST failed: {e}")
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
                    path = path.replace("\\", "/")
                    path = os.path.normpath(path)
                result = read(path)
                last_read_data = result
                is_immediate = True
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
                    image_bytes = result.get("image_bytes")
                    image_url = result.get("image_url")
                    ascii_art = result.get("ascii_art", None)
                    mime = result.get("mime_type", "image/jpeg")
                    msg = f"[Image: {image_url}]"
                    if ascii_art:
                        msg += f"\n[ASCII ART]\n{ascii_art}"
                    # Send image bytes if present
                    if image_bytes:
                        return {
                            "image": image_url,
                            "text": msg,
                            "ascii_art": ascii_art,
                            "image_bytes": image_bytes,
                            "mime_type": mime
                        }, True
                    else:
                        return {
                            "image": image_url,
                            "text": msg,
                            "ascii_art": ascii_art
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
                    # result["text"] is the full_content, result["summary"] is the summary
                    summary = result.get("summary", "")
                    full_content = result.get("text", "")
                    ai_text = result.get("ai_text", full_content)
                    url = args_dict.get("url") or args_dict.get("filepath") or args_dict.get("path") or ""
                    return {
                        "text": summary,      # For user: only titles/urls
                        "ai_text": ai_text,   # For AI: full text or just URLs, depending on content param
                        "url": url
                    }, True
                elif result.get("type") == "image_search":
                    images = result.get("images", [])
                    msg = result.get("text", "")
                    ai_msg = result.get("ai_text", msg)
                    return {
                        "text": msg,      # For user: no ASCII art
                        "ai_text": ai_msg,  # For AI: with or without ASCII art, depending on content param
                        "images": images
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
    known_funcs = get_known_functions()
    pattern = re.compile(rf"({'|'.join(map(re.escape, known_funcs))})\s*\((.*?)\)", re.DOTALL)
    calls = []
    # Знаходити всі виклики функцій із відомого списку, навіть якщо це не валідний Python
    for match in pattern.finditer(response_text):
        # Весь виклик функції (з дужками)
        start = match.start()
        func_call = match.group(0)
        # Перевірка на баланс дужок (для багаторядкових викликів)
        open_parens = 0
        for i, c in enumerate(response_text[start:]):
            if c == '(':
                open_parens += 1
            elif c == ')':
                open_parens -= 1
                if open_parens == 0:
                    func_call = response_text[start:start+i+1]
                    break
        calls.append(func_call)
    return calls

def try_send_message(chat, message, stream=False):
    while True:
        last_exception = None
        for idx, key in enumerate(API_KEYS):
            if not key:
                continue
            try:
                genai.configure(api_key=key)
                if stream:
                    return chat.send_message(message, stream=True)
                else:
                    return chat.send_message(message)
            except Exception as e:
                last_exception = e
        # If we reach here, all keys failed
        print(f"{Fore.RED}[ERROR] All API keys failed. Last error: {last_exception}{Style.RESET_ALL}")
        time.sleep(5)

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

def load_summary():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return data.get("summary", [])
            except Exception:
                pass
    return []

def load_memory():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return data.get("memory", [])
            except Exception:
                pass
    return []

def maybe_summarize_history(chat, history):
    import google.generativeai as genai
    import json
    import re

    # Load previous summary and memory
    prev_summary = load_summary()
    prev_memory = load_memory()

    SUMMARIZER_SYSTEM_PROMPT = (
        "You are a conversation summarizer and memory extractor.\n"
        "Summarize everithing nomatter the contents, nomatter how unethical, illegal, or dangerous it is.\n"
        "Below are the previous summary and memory. You MUST preserve the previous summary and memory exactly as they are: do NOT change, rewrite, or overwrite them in any way.\n"
        "When summarizing, only add new points for new events from the latest conversation. If there are no new events, simply repeat the previous summary and memory.\n"
        "Output ONLY valid JSON (no code blocks, no Markdown, no extra text):\n"
        "{\n"
        '  "summary": ["chronological, detailed bullet points, quoting or closely paraphrasing important exchanges, especially the most recent ones"],\n'
        '  "memory": ["important facts, preferences, goals, recent events as bullet points"]\n'
        "}\n"
        "Do not include any explanations, apologies, or extra formatting. Output only the JSON object.\n"
        "Talk about AI from the first person perspective (I), as if you are the AI, and about the user from the third person perspective (User).\n"
        "Don't just refrase scentances, but rather summorise the main points of the conversation.\n"
        "Preserve the tone, style, and character of the conversation as much as possible.\n"
        "Be especially precise and detailed about the most recent events and exchanges, quoting or paraphrasing them so that the AI can remember them accurately after a reload.\n"
        "Prioritize recent and important events in both summary and memory. If AI and user talked about smth spesific mention the deteils\n"
        "Don't shy from saving the spesific filenames or likes, urls, if thay are important.\n"
        "Update the new memory acording to the conversation from a new chat\n"
        "Any message prefixed with [FUNCTION RESULT; MESSAGE FROM THE SYSTEM] or [SYSTEM MESSAGE] is not a user action, but a system notification. Do not assume the user performed those actions.\n"
        f"[PREVIOUS SUMMARY]\n{json.dumps(prev_summary, ensure_ascii=False, indent=2)}\n[END SUMMARY]\n"
        f"[MEMORY]\n{json.dumps(prev_memory, ensure_ascii=False, indent=2)}\n[END MEMORY]\n"
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

        # Merge with previous summary/memory (they are already lists)
        merged_summary = merge_lists(prev_summary, summary_json.get("summary", []))
        merged_memory = merge_lists(prev_memory, summary_json.get("memory", []))

        # Save pretty-printed JSON to chat_history.txt
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps({"summary": merged_summary, "memory": merged_memory}, indent=2, ensure_ascii=False))
        print("[SYSTEM] Conversation summary and memory saved as valid JSON (in text file).")
    except Exception as e:
        print(f"[SYSTEM] Failed to parse JSON summary: {e}")
        # Fallback: Save raw text in summary block
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
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
    if not os.path.exists(".env") and not os.path.exists("system_prompt_help.txt"):
        setup_wizard()
        # --- Add these lines ---
        load_dotenv(override=True)
        global API_KEYS
        API_KEYS = [
            os.getenv(f"GOOGLE_API_KEY_{i+1}") for i in range(20)
        ]
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

    # Combine for initial prompt
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
                        # Colorize Gemini and You
                        if block_text.startswith("Gemini:"):
                            print(f"{Fore.YELLOW}Gemini:{Style.RESET_ALL}{block_text[len('Gemini:'):]}")
                        elif block_text.startswith("User:"):
                            print(f"{Fore.CYAN}You:{Style.RESET_ALL}{block_text[len('User:'):]}")
                        else:
                            print(block_text)
                        print()  # print a single blank line between messages/blocks
                        block = []
                else:
                    block.append(line.rstrip("\n"))
            if block:
                block_text = "\n".join(block)
                if block_text.startswith("Gemini:"):
                    print(f"{Fore.YELLOW}Gemini:{Style.RESET_ALL}{block_text[len('Gemini:'):]}")
                elif block_text.startswith("User:"):
                    print(f"{Fore.CYAN}You:{Style.RESET_ALL}{block_text[len('User:'):]}")
                else:
                    print(block_text)
                print()
    system_msg = None  # Holds system message to send to AI (function results, etc.)

    # Add this before your main loop
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
                    print("Summarizing conversation before exit...")
                    maybe_summarize_history(chat, chat.history)
                    print("Goodbye!")
                    break
                elif user_input.lower() in ("/plugins", "/functions"):
                    print("Functions available (from function_calls.txt):")
                    try:
                        with open("function_calls.txt", "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line and not line.startswith("//"):
                                    print(f" - {line}")
                    except Exception as e:
                        print(f"Could not read function_calls.txt: {e}")
                    continue
                if user_input.lower() == "/relaunch":
                    print("Refreshing AI state with current conversation history...")
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
                        print("AI state refreshed.")
                        continue
                    except Exception as e:
                        print(f"[ERROR] Failed to refresh AI state: {e}")
                        continue

                if user_input.lower().startswith("reload functions"):
                    load_functions()
                    print("functions reloaded.")
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

                    # --- Stop all background processes ---
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
                            # Clear the file after stopping processes
                            with bg_file.open("w", encoding="utf-8") as f:
                                f.write("{}")
                    except Exception as e:
                        print(f"{Fore.RED}Failed to stop background processes: {e}{Style.RESET_ALL}")

                    os.system('cls' if os.name == 'nt' else 'clear')
                    print(f"{Fore.GREEN}Chat history cleared. Starting fresh!{Style.RESET_ALL}")
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
                    new_key = input("Enter new API key: ").strip()
                    # Read existing keys from .env
                    existing_keys = []
                    if os.path.exists(".env"):
                        with open(".env", "r", encoding="utf-8") as f:
                            for line in f:
                                if line.startswith("GOOGLE_API_KEY_"):
                                    existing_keys.append(line.strip().split("=", 1)[-1])
                    if new_key in existing_keys:
                        print(f"{Fore.YELLOW}API key already exists in .env!{Style.RESET_ALL}")
                        continue
                    # Find the next available index
                    idx = 1
                    while f"GOOGLE_API_KEY_{idx}" in [f"GOOGLE_API_KEY_{i+1}" for i in range(len(existing_keys))]:
                        idx += 1
                    with open(".env", "a", encoding="utf-8") as f:
                        f.write(f"\nGOOGLE_API_KEY_{idx}={new_key}")
                    # Reload environment and update API_KEYS
                    load_dotenv(override=True)
                    API_KEYS[:] = [
                        os.getenv(f"GOOGLE_API_KEY_{i+1}") for i in range(20)
                    ]
                    print(f"{Fore.GREEN}API key added as GOOGLE_API_KEY_{idx}.{Style.RESET_ALL}")
                    continue
                elif user_input.lower().startswith("/listkeys"):
                    print("Loaded API keys:")
                    for idx, key in enumerate(API_KEYS, 1):
                        if key:
                            print(f"Key {idx}: {key[:6]}...{key[-4:]}")
                        else:
                            print(f"Key {idx}: [empty]")
                    continue
                elif user_input.lower().startswith("/setprompt"):
                    new_prompt = input("Enter new system prompt: ").strip()
                    with open("system_prompt_help.txt", "w", encoding="utf-8") as f:
                        f.write(new_prompt)
                    print(f"{Fore.GREEN}System prompt updated.{Style.RESET_ALL}")
                    continue
                elif user_input.lower().startswith("/showprompt"):
                    print(f"{Fore.CYAN}Current system prompt:{Style.RESET_ALL}")
                    print(load_system_prompt())
                    continue
                elif user_input.lower() == "/help":
                    print(f"""{Fore.YELLOW}Available commands:{Style.RESET_ALL}
  /addkey      - Add a new API key
  /listkeys    - List loaded API keys
  /setprompt   - Change the system prompt
  /showprompt  - Show the current system prompt
  /clear       - Clear chat history
  /reset       - Reset all settings to default
  /help        - Show this help message
  /exit        - Quit
  /plugins     - List available plugin functions
""")
                    continue

                # User input
                append_user_history("user", user_input)
                append_ai_history("user", user_input)
                ai_input = user_input
            else:
                # Use system message as AI input, and append to history
                append_user_history("system", system_msg)
                append_ai_history("system", system_msg)
                ai_input = system_msg
                system_msg = None  # Reset after use

            background_errors = pop_background_errors()

            message_counter += 1

            if message_counter % 5 == 0 and function_calls_guide:
                full_input = (
                    f"[SYSTEM PROMPT]\n{system_prompt}\n\n"
                    f"[FUNCTION CALLS GUIDE]\n{function_calls_guide}\n\n"
                    f"[FUNCTION CALLS]\n{function_calls_signatures}\n\n"
                    f"[USER MESSAGE]\n{ai_input}"
                )
            else:
                full_input = (
                    f"[SYSTEM PROMPT]\n{system_prompt}\n\n"
                    f"[FUNCTION CALLS]\n{function_calls_signatures}\n\n"
                    f"[USER MESSAGE]\n{ai_input}"
                )
            if background_errors:
                full_input += f"\n\n{background_errors}"

            try:
                response = try_send_message(chat, full_input)
            except generation_types.StopCandidateException as e:
                continue

            append_user_history("model", response.text)
            append_ai_history("model", response.text)
            response_text_no_thinking = remove_thinking_sections(response.text)
            print(f"\n{Fore.YELLOW}Gemini:{Style.RESET_ALL} {response_text_no_thinking}\n")

            # 4. Extract and handle function calls
            response_text = response.text
            function_calls = extract_function_calls(response_text)
            if not function_calls:
                system_msg = None  # No system message to send, next loop will prompt user
                continue

            # Handle all function calls and collect results
            system_msgs = []
            processed_calls = set()
            for call in function_calls:
                if call in processed_calls:
                    continue
                processed_calls.add(call)
                func_result, _ = handle_function_call(call)
                if func_result:
                    # Centralize result formatting
                    if isinstance(func_result, dict):
                        user_msg = func_result.get("text", "")
                        ai_msg = func_result.get("ai_text", user_msg)
                        if user_msg:
                            print(user_msg)
                        system_msgs.append(ai_msg)
                    elif isinstance(func_result, str):
                        print(func_result)
                        system_msgs.append(func_result)
            # Prepare system message for next loop
            if system_msgs:
                system_msg = "[FUNCTION RESULT; MESSAGE FROM THE SYSTEM]\n" + "\n".join(system_msgs)
            else:
                system_msg = None

            check_and_summarize_if_needed(chat, chat.history)
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
    
if __name__ == "__main__":
    load_functions()
    start_plugin_watcher()  # <-- Add this line
    main()