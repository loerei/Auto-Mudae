from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from mudae.ouro.oh_config import OhConfig


@dataclass
class OhCell:
    row: int
    col: int
    emoji: str
    color: str
    custom_id: Optional[str]
    disabled: bool
    clickable: bool


@dataclass
class OhGrid:
    rows: int
    cols: int
    cells: List[List[OhCell]]

    def iter_cells(self):
        for row in self.cells:
            for cell in row:
                yield cell


def _get_emoji_name(component: Dict[str, Any]) -> str:
    emoji = component.get("emoji")
    if isinstance(emoji, dict):
        name = emoji.get("name")
        if isinstance(name, str):
            return name
    return ""


def parse_oh_message(message: Dict[str, Any], config: OhConfig) -> Optional[OhGrid]:
    components = message.get("components", [])
    if not isinstance(components, list) or not components:
        return None

    rows: List[List[OhCell]] = []
    for row_idx, row in enumerate(components):
        row_components: List[Any]
        if isinstance(row, dict):
            row_components = row.get("components", [])
        elif isinstance(row, list):
            row_components = row
        else:
            continue
        if not isinstance(row_components, list):
            continue
        row_cells: List[OhCell] = []
        for col_idx, component in enumerate(row_components):
            if not isinstance(component, dict):
                continue
            emoji_name = _get_emoji_name(component)
            color = config.emoji_map.get(emoji_name, "UNKNOWN")
            custom_id = component.get("custom_id")
            disabled = bool(component.get("disabled", False))
            clickable = (not disabled) and bool(custom_id)
            row_cells.append(
                OhCell(
                    row=row_idx,
                    col=col_idx,
                    emoji=emoji_name,
                    color=color,
                    custom_id=str(custom_id) if custom_id else None,
                    disabled=disabled,
                    clickable=clickable,
                )
            )
        if row_cells:
            rows.append(row_cells)

    if not rows:
        return None

    max_cols = max(len(r) for r in rows)
    # Normalize rows to same length if needed (pad with UNKNOWN cells).
    for r_idx, row in enumerate(rows):
        if len(row) < max_cols:
            for c_idx in range(len(row), max_cols):
                row.append(
                    OhCell(
                        row=r_idx,
                        col=c_idx,
                        emoji="",
                        color="UNKNOWN",
                        custom_id=None,
                        disabled=True,
                        clickable=False,
                    )
                )

    return OhGrid(rows=len(rows), cols=max_cols, cells=rows)


def diagnose_oh_message(message: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(message, dict):
        return {"error": "message_not_dict"}
    components = message.get("components", None)
    info: Dict[str, Any] = {
        "components_type": type(components).__name__,
        "components_len": len(components) if isinstance(components, list) else None,
        "rows": [],
    }
    if not isinstance(components, list):
        return info
    for row_idx, row in enumerate(components[:5]):
        row_info: Dict[str, Any] = {
            "row_index": row_idx,
            "row_type": type(row).__name__,
        }
        if isinstance(row, dict):
            row_info["row_keys"] = list(row.keys())[:5]
            row_components = row.get("components", None)
        elif isinstance(row, list):
            row_components = row
        else:
            row_components = None
        row_info["row_components_type"] = type(row_components).__name__
        if isinstance(row_components, list):
            row_info["row_components_len"] = len(row_components)
            emoji_names: List[str] = []
            for comp in row_components[:5]:
                if isinstance(comp, dict):
                    emoji_names.append(_get_emoji_name(comp))
            row_info["emoji_names"] = emoji_names
        info["rows"].append(row_info)
    return info


def summarize_grid(grid: OhGrid, hidden_color: str = "HIDDEN") -> Dict[str, Any]:
    unknown_positions: List[Tuple[int, int]] = []
    known_positions: List[Tuple[int, int]] = []
    clickable_positions: List[Tuple[int, int]] = []
    for cell in grid.iter_cells():
        if cell.clickable:
            clickable_positions.append((cell.row, cell.col))
            if cell.color == hidden_color:
                unknown_positions.append((cell.row, cell.col))
            else:
                known_positions.append((cell.row, cell.col))

    return {
        "rows": grid.rows,
        "cols": grid.cols,
        "clickable_positions": clickable_positions,
        "unknown_positions": unknown_positions,
        "known_positions": known_positions,
        "unknown_count": len(unknown_positions),
        "known_count": len(known_positions),
    }



