import random
from typing import Dict, Iterable, List, Tuple, Optional


class OhSolver:
    def __init__(
        self,
        priors: Dict[str, float],
        expected_values: Dict[str, float],
        reveal_counts: Dict[str, int],
        monte_carlo_samples: int = 64,
        seed: Optional[int] = None,
    ) -> None:
        self.priors = {k: float(v) for k, v in priors.items() if v > 0}
        total = sum(self.priors.values())
        if total <= 0:
            # fallback to uniform over provided colors
            colors = list(expected_values.keys())
            uniform = 1.0 / max(1, len(colors))
            self.priors = {c: uniform for c in colors}
        else:
            self.priors = {k: v / total for k, v in self.priors.items()}

        self.expected_values = {k: float(v) for k, v in expected_values.items()}
        self.reveal_counts = {k: int(v) for k, v in reveal_counts.items()}
        self.samples = max(1, int(monte_carlo_samples))
        self._rng = random.Random(seed)
        self._cache: Dict[Tuple[int, int, Tuple[float, ...]], float] = {}

    def _normalize_known(self, values: Iterable[float]) -> Tuple[float, ...]:
        return tuple(sorted(round(float(v), 4) for v in values))

    def _merge_known(self, known: Tuple[float, ...], additions: List[float]) -> Tuple[float, ...]:
        if not additions:
            return known
        merged = list(known)
        merged.extend(additions)
        return self._normalize_known(merged)

    def _sample_color(self) -> str:
        r = self._rng.random()
        acc = 0.0
        for color, p in self.priors.items():
            acc += p
            if r <= acc:
                return color
        # fallback last
        return next(iter(self.priors))

    def _sample_colors(self, n: int) -> List[str]:
        return [self._sample_color() for _ in range(max(0, n))]

    def expected_value(
        self,
        clicks_left: int,
        unknown_count: int,
        known_values: Iterable[float],
    ) -> float:
        known = self._normalize_known(known_values)
        key = (int(clicks_left), int(unknown_count), known)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        if clicks_left <= 0:
            self._cache[key] = 0.0
            return 0.0

        if unknown_count <= 0 and not known:
            self._cache[key] = 0.0
            return 0.0

        best = -1.0

        # exploit: click best known value
        if known:
            best_known = max(known)
            remaining_known = list(known)
            remaining_known.remove(best_known)
            ev_exploit = best_known + self.expected_value(
                clicks_left - 1,
                unknown_count,
                remaining_known,
            )
            best = max(best, ev_exploit)

        # explore: click an unknown
        if unknown_count > 0:
            ev_explore = self._expected_explore(clicks_left, unknown_count, known)
            best = max(best, ev_explore)

        if best < 0:
            best = 0.0
        self._cache[key] = best
        return best

    def _expected_explore(
        self,
        clicks_left: int,
        unknown_count: int,
        known: Tuple[float, ...],
    ) -> float:
        if clicks_left <= 0 or unknown_count <= 0:
            return 0.0

        total = 0.0
        for color, p in self.priors.items():
            if p <= 0:
                continue
            immediate = self.expected_values.get(color, 0.0)
            reveal_n = max(0, min(self.reveal_counts.get(color, 0), unknown_count - 1))
            if reveal_n <= 0:
                future = self.expected_value(
                    clicks_left - 1,
                    unknown_count - 1,
                    known,
                )
                total += p * (immediate + future)
                continue

            future_sum = 0.0
            for _ in range(self.samples):
                revealed_colors = self._sample_colors(reveal_n)
                revealed_values = [self.expected_values.get(c, 0.0) for c in revealed_colors]
                new_known = self._merge_known(known, revealed_values)
                future_sum += self.expected_value(
                    clicks_left - 1,
                    unknown_count - 1 - reveal_n,
                    new_known,
                )
            future = future_sum / float(self.samples)
            total += p * (immediate + future)
        return total

    def choose_action(
        self,
        clicks_left: int,
        unknown_count: int,
        known_values: Iterable[float],
    ) -> Tuple[str, float, float]:
        known = self._normalize_known(known_values)
        ev_exploit = -1.0
        ev_explore = -1.0

        if known:
            best_known = max(known)
            remaining_known = list(known)
            remaining_known.remove(best_known)
            ev_exploit = best_known + self.expected_value(
                clicks_left - 1,
                unknown_count,
                remaining_known,
            )

        if unknown_count > 0:
            ev_explore = self._expected_explore(clicks_left, unknown_count, known)

        if ev_explore > ev_exploit:
            return ("explore", ev_exploit, ev_explore)
        return ("exploit", ev_exploit, ev_explore)
