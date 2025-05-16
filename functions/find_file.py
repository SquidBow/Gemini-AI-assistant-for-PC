import os
import subprocess

def find_paths(name, start_dir="", search_type="any", max_results=20, exact_match=False):
    """
    Searches for files or folders by name using Everything CLI (ES.exe 1.1.0.27 syntax).
    If start_dir is provided, filters results to only include those in that directory (and subdirectories).
    If exact_match is True, only returns files whose basename matches name exactly (case-insensitive).
    Returns a user-friendly formatted string.
    """
    # Get the absolute path to es.exe relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    es_exe = os.path.join(script_dir, "Everything", "es.exe")
    es_exe = os.path.normpath(es_exe)
    if not os.path.exists(es_exe):
        return f"ES.exe not found at {es_exe}. Please check the path."

    # Always search globally, filter in Python
    query_parts = [name]
    if search_type in ("folder", "directory", "directories"):
        query_parts.append(f'folder:{name}')
    elif search_type == "file":
        query_parts.append(f'file:{name}')
    search_text = " ".join(query_parts)

    args = [es_exe, search_text]
    print("Running:", args)  # Debug print

    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="mbcs", check=True)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]

        # Filter results by start_dir if provided
        if start_dir and start_dir.strip():
            norm_start = os.path.normpath(start_dir).lower()
            lines = [line for line in lines if os.path.normpath(line).lower().startswith(norm_start)]

        # Filter for exact filename match if requested
        if exact_match:
            name_lower = name.lower()
            lines = [line for line in lines if os.path.basename(line).lower() == name_lower]

        if lines and len(lines) > max_results:
            lines = lines[:max_results]
        if not lines:
            return f"No {search_type} named '{name}' found."
        formatted = f"Found the following {search_type}s:\n" + "\n".join(
            f"{i+1}. {line}" for i, line in enumerate(lines)
        )
        return formatted
    except Exception as e:
        return f"An unexpected error occurred with ES.exe: {e}"