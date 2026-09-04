"""File tool permissions — controls which file operations Flora may use."""
from app.config import Config

FILE_TOOL_PERMISSION_MAP = {
    "list_user_files": "list",
    "read_user_file": "read",
    "write_user_file": "write",
    "save_text_as_file": "write",
    "delete_user_file": "delete",
}

ALL_FILE_TOOLS = set(FILE_TOOL_PERMISSION_MAP.keys())


def get_allowed_file_tools() -> set[str]:
    return {
        tool for tool, perm in FILE_TOOL_PERMISSION_MAP.items()
        if perm in Config.FILE_PERMISSIONS
    }


def is_file_tool_allowed(tool_name: str) -> bool:
    if tool_name not in FILE_TOOL_PERMISSION_MAP:
        return True
    return tool_name in get_allowed_file_tools()


def filter_tool_descriptions(descriptions: dict) -> dict:
    allowed = get_allowed_file_tools()
    return {
        name: desc for name, desc in descriptions.items()
        if name not in ALL_FILE_TOOLS or name in allowed
    }


def permissions_summary() -> str:
    perms = ", ".join(sorted(Config.FILE_PERMISSIONS)) or "нет"
    tools = ", ".join(sorted(get_allowed_file_tools())) or "нет"
    return f"Разрешения файлов: {perms}. Доступные инструменты: {tools}."
