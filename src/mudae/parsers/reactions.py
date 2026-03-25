from typing import Any, Dict, List, Optional, Tuple, cast

from mudae.config import vars as Vars

_KAKERA_PRIORITY = {
    'kakerap': 10,
    'kakerac': 9,
    'kakeral': 8,
    'kakeraw': 7,
    'kakerar': 6,
    'kakerao': 5,
    'kakerad': 4,
    'kakeray': 3,
    'kakerag': 2,
    'kakerat': 1,
    'kakerab': 0,
    'kakera': 0
}


def _build_kakera_priority() -> Dict[str, int]:
    priority = dict(_KAKERA_PRIORITY)
    overrides = getattr(Vars, "KAKERA_REACTION_PRIORITY", {})
    if not isinstance(overrides, dict):
        return priority
    for raw_name, raw_value in overrides.items():
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip().lower()
        if not name:
            continue
        if isinstance(raw_value, bool):
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        priority[name] = value
    return priority


_ACTIVE_KAKERA_PRIORITY = _build_kakera_priority()


def _iter_message_components(message: Dict[str, Any]) -> List[Tuple[Tuple[int, int], Dict[str, Any]]]:
    components = message.get('components', [])
    if not isinstance(components, list):
        return []
    flattened: List[Tuple[Tuple[int, int], Dict[str, Any]]] = []
    for row_index, row in enumerate(components):
        if not isinstance(row, dict):
            continue
        row_components = row.get('components', [])
        if not isinstance(row_components, list):
            continue
        for comp_index, component in enumerate(row_components):
            if not isinstance(component, dict):
                continue
            flattened.append(((row_index, comp_index), component))
    return flattened


def _collect_reaction_buttons(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    buttons: List[Dict[str, Any]] = []
    for order, component in _iter_message_components(message):
        if component.get('disabled'):
            continue
        emoji = component.get('emoji')
        if not isinstance(emoji, dict):
            continue
        name = emoji.get('name', '')
        if not isinstance(name, str) or not name:
            continue
        if not component.get('custom_id'):
            continue
        buttons.append({
            'emoji_name': name,
            'component': component,
            'order': order
        })
    return buttons


def _is_sphere_emoji(name: str) -> bool:
    lowered = name.lower()
    if lowered == 'sp':
        return True
    return lowered.startswith('sp') and len(lowered) == 3


def _kakera_priority(name: str) -> int:
    return _ACTIVE_KAKERA_PRIORITY.get(name.lower(), -1)


def _is_reactable_kakera(name: str) -> bool:
    # Priority 0 is treated as skipped.
    return _kakera_priority(name) != 0


def _group_reaction_buttons(message: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    buttons = _collect_reaction_buttons(message)
    spheres: List[Dict[str, Any]] = []
    kakera: List[Dict[str, Any]] = []
    for button in buttons:
        name = button.get('emoji_name', '')
        if not isinstance(name, str):
            continue
        if _is_sphere_emoji(name):
            spheres.append(button)
        elif 'kakera' in name.lower() and _is_reactable_kakera(name):
            kakera.append(button)
    spheres.sort(key=lambda b: b.get('order', (0, 0)))
    kakera.sort(key=lambda b: (-_kakera_priority(cast(str, b.get('emoji_name', ''))), b.get('order', (0, 0))))
    return spheres, kakera


def _message_has_kakera_button(message: Dict[str, Any]) -> bool:
    """Check whether a message has kakera buttons."""
    try:
        for button in _collect_reaction_buttons(message):
            name = button.get('emoji_name', '')
            if isinstance(name, str) and 'kakera' in name.lower():
                return True
    except (KeyError, IndexError, TypeError, AttributeError):
        return False
    return False


def _find_claim_button(message: Dict[str, Any]) -> Optional[str]:
    """Return the custom_id for a non-kakera, non-sphere claim button if present."""
    try:
        for _order, component in _iter_message_components(message):
            if component.get('disabled'):
                continue
            if component.get('type') != 2:
                continue
            custom_id = component.get('custom_id')
            if not custom_id:
                continue
            emoji = component.get('emoji')
            if isinstance(emoji, dict):
                name = emoji.get('name', '')
                if isinstance(name, str) and name:
                    if _is_sphere_emoji(name) or 'kakera' in name.lower():
                        continue
            return cast(str, custom_id)
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return None
