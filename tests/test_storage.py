from app.storage import FileStorage


def _hit():
    return {
        "id": "JORFTEXT000000001",
        "title": "Arrêté de nomination",
        "matches": [
            {"name": "Lobna Marsaoui", "level": "full",
             "matched_tokens": ["lobna", "marsaoui"], "snippet": "...Lobna Marsaoui..."}
        ],
    }


def test_file_storage_processed_roundtrip(tmp_path):
    st = FileStorage(str(tmp_path / "state.json"), str(tmp_path / "matches.jsonl"))
    assert st.get_processed() == set()

    st.mark_processed("JORFCONT001", "JORF n°1")
    st.mark_processed("JORFCONT002", "JORF n°2")
    assert st.get_processed() == {"JORFCONT001", "JORFCONT002"}

    # Idempotent: re-marking doesn't duplicate.
    st.mark_processed("JORFCONT001", "JORF n°1")
    assert st.get_processed() == {"JORFCONT001", "JORFCONT002"}


def test_file_storage_match_log(tmp_path):
    st = FileStorage(str(tmp_path / "state.json"), str(tmp_path / "matches.jsonl"))
    st.log_matches("JORFCONT001", "JORF n°1", [_hit()])

    recent = st.recent_matches()
    assert len(recent) == 1
    assert recent[0]["name"] == "Lobna Marsaoui"
    assert recent[0]["level"] == "full"
    assert recent[0]["matched_tokens"] == ["lobna", "marsaoui"]

    stats = st.stats()
    assert stats["backend"] == "file"
    assert stats["match_count"] == 1
