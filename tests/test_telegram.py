from assistant.telegram import chunks


def test_short_text_is_one_message():
    assert chunks("hi") == ["hi"]
    assert chunks("") == [""]


def test_long_text_splits_on_paragraphs_and_keeps_everything():
    paras = [f"p{i} " * 300 for i in range(6)]  # ~1200-1500 chars each
    text = "\n\n".join(paras)
    out = chunks(text, limit=4096)
    assert all(len(c) <= 4096 for c in out)
    assert len(out) > 1
    assert " ".join(out).split() == text.split()  # nothing dropped
    whole = {p.strip() for p in paras}
    assert all({p.strip() for p in c.split("\n\n")} <= whole for c in out)  # whole paragraphs  # cut between paragraphs only


def test_unbreakable_text_is_hard_cut():
    out = chunks("x" * 10000, limit=4096)
    assert [len(c) for c in out] == [4096, 4096, 1808]
