import re
from typing import Dict, List, Optional, Tuple

REWARD_LINE_RE = re.compile(r"<:([^:>]+):\d+>\s*\*\*\+(\d+)\*\*")
STOCK_RE = re.compile(r"Stock:\s*\*\*([\d,]+)\*\*")


def parse_reward_message(content: str) -> Tuple[List[Dict[str, object]], Optional[int]]:
    entries: List[Dict[str, object]] = []
    if not content:
        return entries, None

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = REWARD_LINE_RE.search(line)
        if not m:
            continue
        try:
            amount = int(m.group(2))
        except ValueError:
            continue
        entries.append({
            "emoji": m.group(1),
            "amount": amount,
            "line": line,
        })

    stock = None
    m = STOCK_RE.search(content)
    if m:
        try:
            stock = int(m.group(1).replace(",", ""))
        except ValueError:
            stock = None
    return entries, stock
