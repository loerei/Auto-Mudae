import json
import os
import tempfile

from mudae.storage.json_array_log import append_json_array, iter_json_array


def test_append_json_array_creates_and_appends() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "log.json")
        append_json_array(path, {"a": 1})
        append_json_array(path, {"b": 2})

        data = json.loads(open(path, "r", encoding="utf-8").read())
        assert data == [{"a": 1}, {"b": 2}]


def test_iter_json_array_streams_items() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "log.json")
        append_json_array(path, {"x": 1})
        append_json_array(path, {"x": 2})
        assert list(iter_json_array(path)) == [{"x": 1}, {"x": 2}]


