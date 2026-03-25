from __future__ import annotations

import json
import pickle
from pathlib import Path

from mudae.ouro import Oc_interactive_solver as OcInteractive
from mudae.ouro import Oq_bot as OqBot
from mudae.ouro import Oq_solver as OqSolver
from mudae.ouro import oh_config as OhConfig
from mudae.storage import coordination as Coordination


def _use_tmp_leases(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Coordination, "LEASE_DIR", tmp_path / "leases")


def test_oh_update_stats_merges_existing_counts(tmp_path: Path, monkeypatch) -> None:
    _use_tmp_leases(tmp_path, monkeypatch)
    stats_path = tmp_path / "oh_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "color_counts": {"RED": 2, "BLUE": 1},
                "total_observed": 3,
                "updated_at": "2026-01-01 00:00:00",
            }
        ),
        encoding="utf-8",
    )

    OhConfig.update_stats(str(stats_path), {"RED": 3, "GREEN": 2})

    payload = json.loads(stats_path.read_text(encoding="utf-8"))
    assert payload["color_counts"] == {"RED": 5, "BLUE": 1, "GREEN": 2}
    assert payload["total_observed"] == 8


def test_oq_learning_state_save_unions_existing_aliases(tmp_path: Path, monkeypatch) -> None:
    _use_tmp_leases(tmp_path, monkeypatch)
    learning_path = tmp_path / "oq_emoji_learning.json"
    learning_path.write_text(
        json.dumps({"version": 1, "higher_than_red_emojis": ["spAlpha"]}),
        encoding="utf-8",
    )

    OqBot._save_emoji_learning_state(
        learning_path,
        {"version": 1, "higher_than_red_emojis": ["spBeta", "spAlpha"]},
    )

    payload = json.loads(learning_path.read_text(encoding="utf-8"))
    assert payload == {
        "version": 1,
        "higher_than_red_emojis": ["spAlpha", "spBeta"],
    }


def test_oq_solver_uses_policy_specific_cache_paths() -> None:
    beam_path = OqSolver._state_cache_path("beam3_v1", "beam_k=3")
    exact_path = OqSolver._state_cache_path("beam3_v1", "beam_k=5")
    beam_first = OqSolver._first_suggestion_cache_path("beam3_v1", "beam_k=3")
    exact_first = OqSolver._first_suggestion_cache_path("beam3_v1", "beam_k=5")

    assert beam_path != exact_path
    assert beam_first != exact_first
    assert beam_path.name.startswith("oq_success_prob_beam3_v1_")
    assert beam_first.name.startswith("oq_first_suggestion_beam3_v1_")


def test_oc_policy_cache_save_merges_existing_entries(tmp_path: Path, monkeypatch) -> None:
    _use_tmp_leases(tmp_path, monkeypatch)
    cache_path = tmp_path / "oc_policy_cache.pkl"
    monkeypatch.setattr(OcInteractive, "_policy_cache_path", lambda: str(cache_path))

    OcInteractive._save_policy_cache({("a",): ("pos-a", 0.1, 1.0)})
    OcInteractive._save_policy_cache({("b",): ("pos-b", 0.2, 2.0)})

    payload = pickle.loads(cache_path.read_bytes())
    assert payload["key"] == OcInteractive._policy_cache_key()
    assert payload["cache"] == {
        ("a",): ("pos-a", 0.1, 1.0),
        ("b",): ("pos-b", 0.2, 2.0),
    }
