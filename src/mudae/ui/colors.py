"""
Color and styling utilities for console output.
Uses ANSI escape codes with colorama fallback for cross-platform support.
"""

import sys
import os

try:
    from colorama import Fore, Back, Style, init  # type: ignore
    COLORAMA_AVAILABLE: bool = True
    init(autoreset=True)  # Auto-reset after each print
except ImportError:
    COLORAMA_AVAILABLE = False
    Fore = None  # type: ignore
    Back = None  # type: ignore
    Style = None  # type: ignore

# ANSI Color Codes (work on Windows 10+ and modern terminals)
class ANSIColors:
    # Foreground colors
    BLACK = '\033[30m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    
    # Background colors
    BG_BLACK = '\033[40m'
    BG_RED = '\033[101m'
    BG_GREEN = '\033[102m'
    BG_YELLOW = '\033[103m'
    BG_BLUE = '\033[104m'
    
    # Text styles
    BOLD = '\033[1m'
    DIM = '\033[2m'
    ITALIC = '\033[3m'
    UNDERLINE = '\033[4m'
    BLINK = '\033[5m'
    
    # Reset
    RESET = '\033[0m'


def use_ansi() -> bool:
    """Check if we should use ANSI codes (Windows 10+ or Unix)."""
    if sys.platform == 'win32':
        # Windows 10+ supports ANSI natively
        return sys.version_info >= (3, 6) and os.environ.get('TERM') != 'dumb'
    return True  # Unix/Linux always supports ANSI


# Choose the appropriate method
ANSI_ENABLED = use_ansi()


def colored(text: str, color: str, style: str = '') -> str:
    """
    Apply color and style to text.
    
    Args:
        text: The text to color
        color: Color code (e.g., ANSIColors.GREEN)
        style: Optional style (e.g., ANSIColors.BOLD)
    
    Returns:
        Colored text string
    """
    if not ANSI_ENABLED:
        return text
    
    return f"{style}{color}{text}{ANSIColors.RESET}"


def success(text: str) -> str:
    """Green text for successful operations."""
    return colored(text, ANSIColors.GREEN, ANSIColors.BOLD)


def error(text: str) -> str:
    """Red text for errors."""
    return colored(text, ANSIColors.RED, ANSIColors.BOLD)


def warning(text: str) -> str:
    """Yellow text for warnings."""
    return colored(text, ANSIColors.YELLOW, ANSIColors.BOLD)


def info(text: str) -> str:
    """Cyan text for information."""
    return colored(text, ANSIColors.CYAN)


def highlight(text: str) -> str:
    """Bright white with underline for important info."""
    return colored(text, ANSIColors.WHITE, ANSIColors.BOLD + ANSIColors.UNDERLINE)


def dimmed(text: str) -> str:
    """Dimmed text for secondary info."""
    return colored(text, ANSIColors.WHITE, ANSIColors.DIM)


# Preset combinations for common log patterns
def format_log_line(log_type: str, message: str) -> str:
    """
    Format a log line with appropriate colors.
    
    Args:
        log_type: Type of log ('success', 'error', 'warning', 'info', 'status')
        message: The message to log
    
    Returns:
        Formatted log string
    """
    if log_type == 'success':
        return f"{success('✅ ' + message)}"
    elif log_type == 'error':
        return f"{error('❌ ' + message)}"
    elif log_type == 'warning':
        return f"{warning('⚠️  ' + message)}"
    elif log_type == 'info':
        return f"{info('ℹ️  ' + message)}"
    elif log_type == 'status':
        return f"{colored(message, ANSIColors.BLUE)}"
    else:
        return message


if __name__ == '__main__':
    # Test colors
    print(success("✅ This is success - GREEN"))
    print(error("❌ This is error - RED"))
    print(warning("⚠️  This is warning - YELLOW"))
    print(info("ℹ️  This is info - CYAN"))
    print(highlight("🌟 This is highlighted - BOLD WHITE"))
    print(dimmed("🔇 This is dimmed"))
    print(colored("🎨 Custom color", ANSIColors.MAGENTA, ANSIColors.BOLD))
