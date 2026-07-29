from typing import Optional, Tuple, List, Dict, Any, cast
import re


def extractCardInfo(message: Dict[str, Any]) -> Optional[Tuple[str, str, int]]:
    """Extract character name, series, and kakera from a card message."""
    try:
        embeds = message.get('embeds', [])
        if not embeds:
            return None
        embed = embeds[0]
        author = embed.get('author', {})
        name = author.get('name') or embed.get('title')
        if not name:
            return None
        description = embed.get('description', '')
        if not isinstance(description, str):
            description = ""
        lines = [line.strip() for line in description.split('\n') if line.strip()]
        series = lines[0].replace('**', '').strip() if lines else ""
        kakera_match = re.search(r'\*\*([0-9,]+)\*\*<:kakera', description)
        if not kakera_match:
            kakera_match = re.search(r'([0-9,]+)\s*<:kakera', description)
        if not kakera_match:
            return None
        kakera = int(kakera_match.group(1).replace(',', ''))
        return (cast(str, name), series, kakera)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None


def extractCardImageUrl(message: Dict[str, Any]) -> Optional[str]:
    """Extract the embed image URL from a card message."""
    try:
        embeds = message.get('embeds', [])
        if not embeds:
            return None
        embed = embeds[0]
        image = embed.get('image')
        if isinstance(image, dict):
            url = image.get('url')
            if url:
                return str(url)
        return None
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def extractKeyCounts(message: Dict[str, Any]) -> List[int]:
    """Extract key counts from a card description (e.g., (**4**) on key lines)."""
    try:
        embeds = message.get('embeds', [])
        if not embeds:
            return []
        embed = embeds[0]
        description = embed.get('description', '')
        if not isinstance(description, str):
            return []
        matches = re.findall(r'<:[^:]*key[^:]*:\d+>\s*\(\*\*([0-9,]+)\*\*\)', description)
        return [int(match.replace(',', '')) for match in matches if match]
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return []
