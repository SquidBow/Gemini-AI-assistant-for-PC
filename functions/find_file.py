import os
import subprocess

def find_paths(name, start_dir="C:/", search_type="any", max_results=20):
    """
    Searches for files or folders by name using Everything CLI (ES.exe 1.1.0.27 syntax).
    Returns a user-friendly formatted string.
    """
    es_exe = r"C:\Other Programs\Personal\Everything\es.exe"  # Adjust path if needed
    if not os.path.exists(es_exe):
        return "ES.exe not found. Please check the path."

    # Add path filter if start_dir is specified
    query_parts = []
    if start_dir and start_dir != "C:/":
        query_parts.append(start_dir)
    if search_type in ("folder", "directory", "directories"):
        query_parts.append(f'folder:{name}')
    elif search_type == "file":
        query_parts.append(f'file:{name}')
    else:
        query_parts.append(name)
    search_text = " ".join(query_parts)

    args = [es_exe, search_text]

    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="mbcs", check=True)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if lines and len(lines) > max_results:
            lines = lines[:max_results]
        if not lines:
            return f"No {search_type} named '{name}' found in {start_dir}."
        formatted = f"Found the following directories in {start_dir}:\n" + "\n".join(
            f"{i+1}. {line}" for i, line in enumerate(lines)
        )
        return formatted
    except Exception as e:
        return f"An unexpected error occurred with ES.exe: {e}"