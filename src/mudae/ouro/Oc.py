"""
Oc.py - Ouro Chest ($oc) Strategy Solver
Simulates the sphere-finding game and uses constraint-based strategy to find the red sphere
"""

import random
from typing import List, Tuple, Set, Dict, Optional
from enum import Enum
import os
import time
import pickle
import atexit
from itertools import combinations
from multiprocessing import Pool, cpu_count, current_process

from mudae.paths import OURO_CACHE_DIR, ensure_runtime_dirs

class SphereColor(Enum):
    """Sphere colors with their point values"""
    RED = 150  # Target sphere
    ORANGE = 90
    YELLOW = 35
    GREEN = 35
    TEAL = 35
    BLUE = 10


# All positions for clicking (25 total)
ALL_POSITIONS = [(r,c) for r in range(5) for c in range(5)]
# Valid RED positions (exclude center, 24 total)
VALID_POSITIONS = [(r,c) for r in range(5) for c in range(5) if not (r==2 and c==2)]

LIKELIHOOD_CACHE_VERSION = "exact_v1"
LIKELIHOOD_CACHE_FILENAME = f"oc_likelihoods_{LIKELIHOOD_CACHE_VERSION}.pkl"
PARALLEL_EVAL = True  # evaluate root actions in parallel for better CPU use
PARALLEL_WORKERS = 0  # 0 = cpu_count()
PARALLEL_MIN_ACTIONS = 6  # avoid overhead when few choices remain

ensure_runtime_dirs()


def _likelihood_cache_path() -> str:
    return os.fspath(OURO_CACHE_DIR / LIKELIHOOD_CACHE_FILENAME)


def _build_likelihoods_exact() -> Dict:
    """
    Build exact likelihoods P(obs_color | red_pos, click_pos) by enumeration.
    """
    start = time.time()
    likelihood = {}
    
    # Initialize for all positions (can click center, but RED never spawns there)
    for click in ALL_POSITIONS:
        likelihood[click] = {}
        for red in VALID_POSITIONS:
            likelihood[click][red] = {col: 0 for col in SphereColor}
    
    def get_adjacent(r0, c0):
        adj = []
        for dr, dc in [(-1,0), (1,0), (0,-1), (0,1)]:
            nr, nc = r0+dr, c0+dc
            if 0 <= nr < 5 and 0 <= nc < 5:
                adj.append((nr,nc))
        return adj
    
    def get_diagonal(r0, c0):
        diag = []
        for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
            nr, nc = r0 + dr, c0 + dc
            while 0 <= nr < 5 and 0 <= nc < 5:
                diag.append((nr, nc))
                nr += dr
                nc += dc
        return diag
    
    def get_row_col(r0, c0):
        positions = []
        for i in range(5):
            if i != c0:
                positions.append((r0, i))
            if i != r0:
                positions.append((i, c0))
        return positions

    boards_per_red = {red: 0 for red in VALID_POSITIONS}

    # Enumerate all valid boards for each RED position
    for red in VALID_POSITIONS:
        r, c = red
        orange_positions = get_adjacent(r, c)
        yellow_positions = get_diagonal(r, c)
        row_col_positions = get_row_col(r, c)
        row_col_set = set(row_col_positions)
        diag_set = set(yellow_positions)

        for oranges in combinations(orange_positions, 2):
            orange_set = set(oranges)
            for yellows in combinations(yellow_positions, 3):
                yellow_set = set(yellows)
                green_candidates = [pos for pos in row_col_positions if pos not in orange_set]
                for greens in combinations(green_candidates, 4):
                    green_set = set(greens)
                    teal_set = (row_col_set | diag_set) - orange_set - yellow_set - green_set - {red}

                    boards_per_red[red] += 1
                    for click_pos in ALL_POSITIONS:
                        if click_pos == red:
                            color = SphereColor.RED
                        elif click_pos in orange_set:
                            color = SphereColor.ORANGE
                        elif click_pos in yellow_set:
                            color = SphereColor.YELLOW
                        elif click_pos in green_set:
                            color = SphereColor.GREEN
                        elif click_pos in teal_set:
                            color = SphereColor.TEAL
                        else:
                            color = SphereColor.BLUE
                        likelihood[click_pos][red][color] += 1
    
    # Normalize to probabilities
    for click in ALL_POSITIONS:
        for red in VALID_POSITIONS:
            total = boards_per_red[red]
            if total == 0:
                continue
            counts = likelihood[click][red]
            for col in list(counts.keys()):
                counts[col] = counts[col] / total
    
    elapsed = time.time() - start
    print(f"[OC] Built exact likelihoods in {elapsed:.1f}s")
    return likelihood


def _load_or_build_likelihoods() -> Dict:
    cache_path = _likelihood_cache_path()
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as f:
                payload = pickle.load(f)
            if isinstance(payload, dict) and payload.get("version") == LIKELIHOOD_CACHE_VERSION:
                print(f"[OC] Loaded cached likelihoods from {os.path.basename(cache_path)}")
                return payload["likelihoods"]
        except Exception:
            pass

    likelihood = _build_likelihoods_exact()
    try:
        with open(cache_path, "wb") as f:
            pickle.dump(
                {"version": LIKELIHOOD_CACHE_VERSION, "likelihoods": likelihood},
                f,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
    except Exception:
        pass
    return likelihood


# Load likelihoods at module initialization
_LIKELIHOODS = _load_or_build_likelihoods()


_WORKER_LIKELIHOODS = None


def _worker_init(likelihoods):
    global _WORKER_LIKELIHOODS
    _WORKER_LIKELIHOODS = likelihoods


def _worker_belief_key(prior: Dict[Tuple[int, int], float]) -> Tuple[float, ...]:
    return tuple(round(prior.get(pos, 0.0), 6) for pos in VALID_POSITIONS)


def _worker_candidate_actions(revealed_mask: int):
    return [(idx, pos) for idx, pos in enumerate(ALL_POSITIONS) if not (revealed_mask & (1 << idx))]


def _worker_search_best_action(prior: Dict[Tuple[int, int], float], revealed_mask: int, clicks_left: int, cache: Dict):
    if clicks_left <= 0 or not prior:
        return None, 0.0

    key = (clicks_left, revealed_mask, _worker_belief_key(prior))
    cached = cache.get(key)
    if cached is not None:
        return cached

    actions = _worker_candidate_actions(revealed_mask)
    if not actions:
        return None, 0.0

    best_pos = None
    best_val = -1.0
    for pos_idx, pos in actions:
        expected = _worker_expected_value_for_action(prior, pos, pos_idx, revealed_mask, clicks_left, cache)
        if expected > best_val:
            best_val = expected
            best_pos = pos

    result = (best_pos, best_val)
    cache[key] = result
    return result


def _worker_expected_value_for_action(
    prior: Dict[Tuple[int, int], float],
    pos: Tuple[int, int],
    pos_idx: int,
    revealed_mask: int,
    clicks_left: int,
    cache: Dict,
) -> float:
    immediate = prior.get(pos, 0.0)
    if clicks_left <= 1:
        return immediate

    obs_probs = {col: 0.0 for col in SphereColor}
    for rp, p in prior.items():
        for col, prob in _WORKER_LIKELIHOODS[pos][rp].items():
            obs_probs[col] += p * prob

    expected = immediate
    next_mask = revealed_mask | (1 << pos_idx)
    for o, P_o in obs_probs.items():
        if P_o == 0 or o == SphereColor.RED:
            continue

        posterior_o = {}
        s = 0.0
        for rp, p in prior.items():
            like = _WORKER_LIKELIHOODS[pos][rp].get(o, 0.0)
            if like > 0:
                posterior_o[rp] = p * like
                s += posterior_o[rp]

        if s == 0:
            continue

        for rp in posterior_o:
            posterior_o[rp] /= s

        _, best_val = _worker_search_best_action(posterior_o, next_mask, clicks_left - 1, cache)
        expected += P_o * best_val

    return expected


def _worker_expected_value_task(args):
    prior, pos, pos_idx, revealed_mask, clicks_left = args
    cache = {}
    return _worker_expected_value_for_action(prior, pos, pos_idx, revealed_mask, clicks_left, cache)


class OcGame:
    """Ouro Chest sphere game simulator"""
    
    def __init__(self, seed: Optional[int] = None):
        """Initialize game with optional seed for reproducibility"""
        if seed is not None:
            random.seed(seed)
        
        self.grid = [[None for _ in range(5)] for _ in range(5)]
        self.red_pos = None
        self.generate_game()
        self.clicks_used = 0
        self.max_clicks = 5
        self.revealed = [[False for _ in range(5)] for _ in range(5)]
        self.click_results = {}  # Store what was clicked
        
    def generate_game(self):
        """Generate a valid game board with red and constrained sphere positions"""
        # Red sphere can't be at center (3,3) - indices 2,2 in 0-indexed
        valid_red_positions = [
            (0, 0), (0, 1), (0, 2), (0, 3), (0, 4),  # Top row
            (1, 0), (1, 1), (1, 2), (1, 3), (1, 4),  # Row 2
            (2, 0), (2, 1), (2, 3), (2, 4),          # Row 3 (skip center)
            (3, 0), (3, 1), (3, 2), (3, 3), (3, 4),  # Row 4
            (4, 0), (4, 1), (4, 2), (4, 3), (4, 4),  # Row 5
        ]
        
        self.red_pos = random.choice(valid_red_positions)
        r, c = self.red_pos
        self.grid[r][c] = SphereColor.RED
        
        # Place orange (adjacent to red)
        orange_positions = self._get_adjacent(r, c)
        for pos in random.sample(orange_positions, min(2, len(orange_positions))):
            self.grid[pos[0]][pos[1]] = SphereColor.ORANGE
        
        # Place yellow (diagonal to red)
        yellow_positions = self._get_diagonal(r, c)
        for pos in random.sample(yellow_positions, min(3, len(yellow_positions))):
            if self.grid[pos[0]][pos[1]] is None:
                self.grid[pos[0]][pos[1]] = SphereColor.YELLOW
        
        # Place green (same row or column as red)
        green_positions = [pos for pos in self._get_row_col(r, c) if self.grid[pos[0]][pos[1]] is None]
        for pos in random.sample(green_positions, min(4, len(green_positions))):
            self.grid[pos[0]][pos[1]] = SphereColor.GREEN
        
        # Place teal (same row, column, or diagonal as red)
        teal_positions = self._get_row_col(r, c) + self._get_diagonal(r, c)
        teal_positions = list(set(teal_positions))  # Remove duplicates
        for pos in teal_positions:
            if self.grid[pos[0]][pos[1]] is None:
                self.grid[pos[0]][pos[1]] = SphereColor.TEAL
        
        # Fill rest with blue
        for r_idx in range(5):
            for c_idx in range(5):
                if self.grid[r_idx][c_idx] is None:
                    self.grid[r_idx][c_idx] = SphereColor.BLUE
    
    def _get_adjacent(self, r: int, c: int) -> List[Tuple[int, int]]:
        """Get orthogonally adjacent positions"""
        adjacent = []
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = r + dr, c + dc
            if 0 <= nr < 5 and 0 <= nc < 5:
                adjacent.append((nr, nc))
        return adjacent
    
    def _get_diagonal(self, r: int, c: int) -> List[Tuple[int, int]]:
        """Get all diagonal positions in line with (r, c)"""
        diagonal = []
        for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
            nr, nc = r + dr, c + dc
            while 0 <= nr < 5 and 0 <= nc < 5:
                diagonal.append((nr, nc))
                nr += dr
                nc += dc
        return diagonal
    
    def _get_row_col(self, r: int, c: int) -> List[Tuple[int, int]]:
        """Get all positions in same row or column (excluding center position)"""
        positions = []
        for i in range(5):
            if i != c:  # Same row, different column
                positions.append((r, i))
            if i != r:  # Same column, different row
                positions.append((i, c))
        return positions
    
    def click(self, r: int, c: int) -> SphereColor:
        """Click a position and reveal it"""
        if self.clicks_used >= self.max_clicks:
            print(f"No more clicks available! ({self.clicks_used}/{self.max_clicks})")
            return None
        
        if self.revealed[r][c]:
            print(f"Position ({r+1},{c+1}) already revealed!")
            return self.grid[r][c]
        
        self.revealed[r][c] = True
        self.clicks_used += 1
        self.click_results[(r, c)] = self.grid[r][c]
        
        return self.grid[r][c]
    
    def get_unrevealed_positions(self) -> List[Tuple[int, int]]:
        """Get all unrevealed positions"""
        unrevealed = []
        for r in range(5):
            for c in range(5):
                if not self.revealed[r][c]:
                    unrevealed.append((r, c))
        return unrevealed
    
    def print_board(self, show_solution: bool = False):
        """Print the game board"""
        print("\n  1 2 3 4 5")
        print("  ---------")
        for r in range(5):
            row_str = f"{r+1}| "
            for c in range(5):
                if self.revealed[r][c]:
                    color = self.grid[r][c].name[0]  # First letter of color
                    row_str += f"{color} "
                elif show_solution:
                    color = self.grid[r][c].name[0]
                    row_str += f"{color} "
                else:
                    row_str += "? "
            print(row_str)
        print()
    
    def is_red_found(self) -> bool:
        """Check if red sphere has been found"""
        r, c = self.red_pos
        return self.revealed[r][c]


class OcSolver:
    """Bayesian + EV Lookahead solver using empirical likelihoods"""
    
    CACHE_ROUND = 6
    _POLICY_CACHE: Dict = {}
    _POOL: Optional[Pool] = None

    def __init__(self, game: OcGame, search_depth: int = 5):
        self.game = game
        self.likelihoods = _LIKELIHOODS
        self.prior = {rp: 1.0/len(VALID_POSITIONS) for rp in VALID_POSITIONS}
        self.threshold = 0.35  # Click RED position if probability >= 35%
        self.strategy_log = []
        self.search_depth = max(1, min(search_depth, self.game.max_clicks))

    def _belief_key(self, prior: Dict[Tuple[int, int], float]) -> Tuple[float, ...]:
        return tuple(round(prior.get(pos, 0.0), self.CACHE_ROUND) for pos in VALID_POSITIONS)

    def _mask_from_revealed(self) -> int:
        mask = 0
        for idx, (r, c) in enumerate(ALL_POSITIONS):
            if self.game.revealed[r][c]:
                mask |= 1 << idx
        return mask

    def _candidate_actions(self, prior: Dict[Tuple[int, int], float], revealed_mask: int):
        actions = []
        for idx, pos in enumerate(ALL_POSITIONS):
            if not (revealed_mask & (1 << idx)):
                actions.append((idx, pos))
        return actions

    @classmethod
    def _get_pool(cls) -> Optional[Pool]:
        if not PARALLEL_EVAL or current_process().name != "MainProcess":
            return None
        if cls._POOL is None:
            workers = PARALLEL_WORKERS if PARALLEL_WORKERS > 0 else cpu_count()
            cls._POOL = Pool(processes=workers, initializer=_worker_init, initargs=(_LIKELIHOODS,))
            atexit.register(cls._close_pool)
        return cls._POOL

    @classmethod
    def _close_pool(cls):
        if cls._POOL is not None:
            cls._POOL.close()
            cls._POOL.join()
            cls._POOL = None

    def _search_best_action(
        self,
        prior: Dict[Tuple[int, int], float],
        revealed_mask: int,
        clicks_left: int,
    ) -> Tuple[Optional[Tuple[int, int]], float]:
        if clicks_left <= 0 or not prior:
            return None, 0.0

        key = (clicks_left, revealed_mask, self._belief_key(prior))
        cached = self._POLICY_CACHE.get(key)
        if cached is not None:
            return cached

        actions = self._candidate_actions(prior, revealed_mask)
        if not actions:
            return None, 0.0

        best_pos = None
        best_val = -1.0
        for pos_idx, pos in actions:
            expected = self._expected_value_for_action(prior, pos, pos_idx, revealed_mask, clicks_left)
            if expected > best_val:
                best_val = expected
                best_pos = pos

        result = (best_pos, best_val)
        self._POLICY_CACHE[key] = result
        return result

    def _expected_value_for_action(
        self,
        prior: Dict[Tuple[int, int], float],
        pos: Tuple[int, int],
        pos_idx: int,
        revealed_mask: int,
        clicks_left: int,
    ) -> float:
        immediate = prior.get(pos, 0.0)
        if clicks_left <= 1:
            return immediate

        obs_probs = {col: 0.0 for col in SphereColor}
        for rp, p in prior.items():
            for col, prob in self.likelihoods[pos][rp].items():
                obs_probs[col] += p * prob

        expected = immediate
        next_mask = revealed_mask | (1 << pos_idx)
        for o, P_o in obs_probs.items():
            if P_o == 0 or o == SphereColor.RED:
                continue

            posterior_o = {}
            s = 0.0
            for rp, p in prior.items():
                like = self.likelihoods[pos][rp].get(o, 0.0)
                if like > 0:
                    posterior_o[rp] = p * like
                    s += posterior_o[rp]

            if s == 0:
                continue

            for rp in posterior_o:
                posterior_o[rp] /= s

            _, best_val = self._search_best_action(posterior_o, next_mask, clicks_left - 1)
            expected += P_o * best_val

        return expected
    
    def bayes_update(self, click_pos: Tuple[int, int], observed_color: SphereColor):
        """Update posterior belief using Bayes rule with empirical likelihoods"""
        new_prior = {}
        total = 0.0
        
        for rp, p in self.prior.items():
            # P(obs | red_at_rp)
            likelihood = self.likelihoods[click_pos][rp].get(observed_color, 0.0)
            val = p * likelihood
            if val > 0:
                new_prior[rp] = val
                total += val
        
        # Normalize
        if total == 0:
            self.prior = {}
            self.strategy_log.append(f"Impossible observation: {observed_color.name} at {click_pos}")
            return
        
        for rp in list(new_prior.keys()):
            new_prior[rp] /= total
        
        self.prior = new_prior
        self.strategy_log.append(f"Updated prior: {len(self.prior)} candidates remain")
    
    def pick_click_ev(self) -> Tuple[int, int]:
        remaining = self.game.max_clicks - self.game.clicks_used
        depth = min(self.search_depth, remaining)
        if depth <= 1:
            return self._pick_click_one_step()

        if not self.prior:
            unrevealed = self.game.get_unrevealed_positions()
            return unrevealed[0] if unrevealed else None

        best_rp, best_p = max(self.prior.items(), key=lambda kv: kv[1])
        if best_p >= 0.999 and not self.game.revealed[best_rp[0]][best_rp[1]]:
            self.strategy_log.append(f"CERTAIN RED: {best_p:.1%} at {best_rp}")
            return best_rp

        revealed_mask = self._mask_from_revealed()
        actions = self._candidate_actions(self.prior, revealed_mask)
        if PARALLEL_EVAL and len(actions) >= PARALLEL_MIN_ACTIONS:
            pool = self._get_pool()
            if pool is not None:
                tasks = [(self.prior, pos, pos_idx, revealed_mask, depth) for pos_idx, pos in actions]
                try:
                    values = pool.map(_worker_expected_value_task, tasks)
                    best_idx = max(range(len(actions)), key=lambda i: values[i])
                    return actions[best_idx][1]
                except Exception:
                    pass

        best_pos, _ = self._search_best_action(self.prior, revealed_mask, depth)
        if best_pos is None:
            return self._pick_click_one_step()
        return best_pos

    def _pick_click_one_step(self) -> Tuple[int, int]:
        """
        Select next click using one-step EV lookahead.
        
        For each unrevealed position, calculate:
        EV(pos) = P(RED at pos now) + E[best next probability after click]
        
        Returns position with highest EV.
        """
        if not self.prior:
            unrevealed = self.game.get_unrevealed_positions()
            return unrevealed[0] if unrevealed else None
        
        # If RED probability is high, click it
        best_rp, best_p = max(self.prior.items(), key=lambda kv: kv[1])
        if best_p >= self.threshold:
            self.strategy_log.append(f"HIGH CONFIDENCE: {best_p:.1%} at {best_rp}")
            return best_rp
        
        # Otherwise, use EV lookahead
        unrevealed = self.game.get_unrevealed_positions()
        best_ev = -1.0
        best_pos = None
        
        for pos in unrevealed:
            # Immediate value: probability RED is at this position
            immediate = self.prior.get(pos, 0.0)
            
            # Expected value after this click: best we can do in next state
            # For each possible observation outcome:
            obs_probs = {col: 0.0 for col in SphereColor}
            for rp, p in self.prior.items():
                for col, prob in self.likelihoods[pos][rp].items():
                    obs_probs[col] += p * prob
            
            expected_next = 0.0
            for o, P_o in obs_probs.items():
                if P_o == 0 or o == SphereColor.RED:
                    continue
                
                # Posterior distribution given this observation
                posterior_o = {}
                s = 0.0
                for rp, p in self.prior.items():
                    like = self.likelihoods[pos][rp].get(o, 0.0)
                    if like > 0:
                        posterior_o[rp] = p * like
                        s += posterior_o[rp]
                
                if s == 0:
                    continue
                
                for rp in posterior_o:
                    posterior_o[rp] /= s
                
                # Best probability we can achieve in next state
                best_next = max(posterior_o.values()) if posterior_o else 0.0
                expected_next += P_o * best_next
            
            # Combined EV: immediate success + expected next success
            ev = immediate + expected_next
            
            if ev > best_ev:
                best_ev = ev
                best_pos = pos
        
        if best_pos is None:
            # Fallback: click position with highest probability
            for pos in sorted(self.prior, key=lambda p: -self.prior[p]):
                if not self.game.revealed[pos[0]][pos[1]]:
                    return pos
            return unrevealed[0] if unrevealed else None
        
        return best_pos
    
    def solve(self) -> bool:
        """Attempt to solve the game using Bayesian inference"""
        print("\n" + "="*50)
        print("Starting Bayesian OC Solver (EV Lookahead)")
        print("="*50)
        
        while self.game.clicks_used < self.game.max_clicks:
            pos = self.pick_click_ev()
            if pos is None:
                break
            
            r, c = pos
            
            # Skip if position already revealed
            if self.game.revealed[r][c]:
                unrevealed = self.game.get_unrevealed_positions()
                if unrevealed:
                    r, c = unrevealed[0]
                else:
                    break
            
            print(f"\n📍 Click {self.game.clicks_used + 1}/5 → Position ({r+1},{c+1})")
            
            sphere = self.game.click(r, c)
            if sphere is None:
                break
            
            print(f"   Result: {sphere.name} (+{sphere.value if sphere != SphereColor.RED else '?'} spheres)")
            print(f"   Candidates remaining: {len(self.prior)}")
            
            if self.game.is_red_found():
                print(f"\nSUCCESS! Found red sphere at ({r+1},{c+1})!")
                return True
            
            # Update beliefs
            self.bayes_update((r, c), sphere)
        
        if self.game.is_red_found():
            print(f"\nSUCCESS! Found red sphere!")
            return True
        else:
            r, c = self.game.red_pos
            print(f"\nFAILED! Red was at ({r+1},{c+1})")
            return False


def _run_single_game(_):
    """Run a single game (for parallel processing)"""
    # Suppress all output
    import io
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    
    try:
        game = OcGame()
        solver = OcSolver(game)
        success = solver.solve()
        return success, game.clicks_used
    finally:
        sys.stdout = old_stdout


def run_simulation_parallel(num_games: int = 10, num_workers: Optional[int] = None):
    """Run simulations in parallel using multiprocessing"""
    if num_workers is None:
        num_workers = cpu_count()
    
    print("\n" + "="*60)
    print(f"Running {num_games} OC Simulations (Parallel, {num_workers} workers)")
    print("="*60)
    
    successes = 0
    clicks_used_total = 0
    
    with Pool(num_workers) as pool:
        results = pool.imap_unordered(_run_single_game, range(num_games), chunksize=10)
        
        for i, (success, clicks) in enumerate(results):
            if success:
                successes += 1
            clicks_used_total += clicks
            
            if (i+1) % max(1, num_games//10) == 0:
                progress = (i+1) / num_games * 100
                print(f"  {progress:.0f}% complete...")
    
    # Print statistics
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    print(f"Success Rate: {successes}/{num_games} ({successes*100//num_games}%)")
    print(f"Avg Clicks Used: {clicks_used_total/num_games:.2f}/5")
    print(f"Successful Games: {successes}")
    print(f"Failed Games: {num_games - successes}")
    print("="*60 + "\n")


def run_simulation(num_games: int = 10, verbose: bool = False, quiet: bool = False):
    """Run multiple game simulations to test strategy"""
    print("\n" + "="*60)
    print(f"Running {num_games} OC Simulations")
    print("="*60)
    
    successes = 0
    clicks_used_total = 0
    
    # Suppress solve() output if quiet mode
    import sys
    if quiet:
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
    
    for game_num in range(num_games):
        game = OcGame()
        solver = OcSolver(game)
        
        if verbose:
            if quiet:
                sys.stdout = old_stdout
            print(f"\n--- Game {game_num + 1} ---")
            if quiet:
                sys.stdout = io.StringIO()
        
        success = solver.solve()
        
        if success:
            successes += 1
            clicks_used_total += game.clicks_used
            if verbose:
                if quiet:
                    sys.stdout = old_stdout
                print(f"   Clicks used: {game.clicks_used}/5")
                if quiet:
                    sys.stdout = io.StringIO()
        else:
            clicks_used_total += game.clicks_used
            if verbose:
                if quiet:
                    sys.stdout = old_stdout
                print(f"   Clicks used: {game.clicks_used}/5 (FAILED)")
                if quiet:
                    sys.stdout = io.StringIO()
    
    # Restore stdout before printing results
    if quiet:
        sys.stdout = old_stdout
    
    # Print statistics
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    print(f"Success Rate: {successes}/{num_games} ({successes*100//num_games}%)")
    print(f"Avg Clicks Used: {clicks_used_total/num_games:.2f}/5")
    print(f"Successful Games: {successes}")
    print(f"Failed Games: {num_games - successes}")
    print("="*60 + "\n")


def interactive_mode():
    """Play a single game interactively"""
    print("\n" + "="*60)
    print("🎮 Interactive OC Game")
    print("="*60)
    
    game = OcGame()
    solver = OcSolver(game)
    
    print("\nGame board (? = unrevealed):")
    game.print_board()
    
    while game.clicks_used < game.max_clicks:
        pos = solver.get_best_click()
        if pos is None:
            break
        
        r, c = pos
        print(f"\n📍 Recommended click: ({r+1},{c+1})")
        response = input("Click this position? (y/n/show): ").lower()
        
        if response == 'show':
            game.print_board(show_solution=True)
            continue
        elif response != 'y':
            try:
                coords = input("Enter position (e.g., 1 3 for row 1, col 3): ").split()
                r, c = int(coords[0]) - 1, int(coords[1]) - 1
            except:
                continue
        
        sphere = game.click(r, c)
        print(f"   Result: {sphere.name} (+{sphere.value if sphere != SphereColor.RED else '?'} spheres)")
        
        solver._eliminate_positions(r, c, sphere)
        game.print_board()
        
        if game.is_red_found():
            print(f"\nSUCCESS! Found red sphere!")
            game.print_board(show_solution=True)
            break
    
    if not game.is_red_found():
        r, c = game.red_pos
        print(f"\nGame Over! Red was at ({r+1},{c+1})")
        game.print_board(show_solution=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "interactive":
        interactive_mode()
    else:
        # Run batch simulations in parallel
        num_simulations = 20
        if len(sys.argv) > 1:
            try:
                num_simulations = int(sys.argv[1])
            except ValueError:
                pass
        
        run_simulation_parallel(num_games=num_simulations)
