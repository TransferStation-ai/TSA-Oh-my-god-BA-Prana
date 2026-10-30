from skills.chat import CHAT_SKILLS, validate_chat_message_arguments
from skills.desktop import (
    DESKTOP_SKILLS,
    DesktopAccess,
    invoke_desktop_skill,
    validate_desktop_arguments,
    validate_desktop_authorization_arguments,
)
from skills.media import MEDIA_SKILLS, invoke_media_skill
from skills.qq_music import (
    QQ_MUSIC_SKILLS,
    invoke_qq_music_skill,
    validate_qq_music_arguments,
)
from skills.terminal import (
    TERMINAL_SKILLS,
    TIME_SKILLS,
    invoke_system_time_skill,
    invoke_terminal_skill,
    validate_system_time_arguments,
    validate_terminal_command,
)
from skills.workspace import (
    WORKSPACE_SKILLS,
    invoke_workspace_skill,
    validate_workspace_skill_arguments,
)
from skills.git import GIT_SKILLS, invoke_git_skill, validate_git_arguments

__all__ = [
    "CHAT_SKILLS",
    "DESKTOP_SKILLS",
    "DesktopAccess",
    "GIT_SKILLS",
    "MEDIA_SKILLS",
    "QQ_MUSIC_SKILLS",
    "TERMINAL_SKILLS",
    "TIME_SKILLS",
    "WORKSPACE_SKILLS",
    "invoke_desktop_skill",
    "invoke_git_skill",
    "invoke_media_skill",
    "invoke_qq_music_skill",
    "invoke_system_time_skill",
    "invoke_terminal_skill",
    "invoke_workspace_skill",
    "validate_chat_message_arguments",
    "validate_desktop_arguments",
    "validate_desktop_authorization_arguments",
    "validate_git_arguments",
    "validate_qq_music_arguments",
    "validate_system_time_arguments",
    "validate_terminal_command",
    "validate_workspace_skill_arguments",
]
