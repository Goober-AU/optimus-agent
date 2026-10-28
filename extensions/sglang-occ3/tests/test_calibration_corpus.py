"""Turn-disjoint pooled calibration, held-out evaluation and matched controls."""
from __future__ import annotations

import functools

import pytest

torch = pytest.importorskip("torch")


def _model(rank, dim, seed=1000):
    generator = torch.Generator().manual_seed(seed)
    return (torch.randn(rank, dim, generator=generator) / rank ** 0.5,
            torch.randn(rank, dim, generator=generator) / rank ** 0.5)


def _make_turns(count=12, rows=60, dim=32, rank=8, kappa=0.9, seed=0, independent=False):
    from occ3_sglang.calibration_corpus import TurnBlock
    left, right = _model(rank, dim)
    other_left, other_right = _model(rank + 1, dim, seed=2000)
    generator = torch.Generator().manual_seed(seed)
    blocks = []
    for index in range(count):
        factor = torch.randn(rows, rank, generator=generator)
        noise_h = torch.randn(rows, dim, generator=generator)
        noise_e = torch.randn(rows, dim, generator=generator)
        hidden = kappa ** 0.5 * (factor @ left) + (1.0 - kappa) ** 0.5 * noise_h
        if independent:
            embeddings = torch.randn(rows, dim, generator=generator)
            hidden = torch.randn(rows, dim, generator=generator)
        else:
            embeddings = kappa ** 0.5 * (factor @ right) + (1.0 - kappa) ** 0.5 * noise_e
        tokens = (kappa ** 0.5 * (torch.randn(rows, rank + 1, generator=generator) @ other_right)
                  + (1.0 - kappa) ** 0.5 * torch.randn(rows, dim, generator=generator))
        blocks.append(TurnBlock(turn_id=f"turn-{index:02d}", hidden=hidden, embeddings=embeddings,
                               reference_embeddings=tokens,
                               wrong_sender_hidden=torch.randn(rows, dim, generator=generator)))
    return blocks


@functools.lru_cache(maxsize=None)
def _corpus(count=12, rows=60, seed=0, independent=False):
    return tuple(_make_turns(count=count, rows=rows, seed=seed, independent=independent))


@functools.lru_cache(maxsize=None)
def _artifact(rows_budget=512):
    from occ3_sglang.calibration_corpus import (pooled_rows, select_calibration_rows,
                                                split_turns)
    from occ3_sglang.statebridge_calibrated import fit_calibration
    fit_blocks, _ = split_turns(_corpus())
    selected, _ = select_calibration_rows(fit_blocks, rows_budget)
    hidden, embeddings = pooled_rows(selected)
    return fit_calibration(hidden, embeddings), float(embeddings.norm(dim=-1).mean())


def test_split_is_turn_disjoint():
    from occ3_sglang.calibration_corpus import split_turns
    blocks = _corpus()
    fit_blocks, eval_blocks = split_turns(blocks)
    assert {value.turn_id for value in fit_blocks}.isdisjoint(
        {value.turn_id for value in eval_blocks})
    assert sorted([value.turn_id for value in fit_blocks + eval_blocks]) == sorted(
        [value.turn_id for value in blocks])
    assert len(fit_blocks) + len(eval_blocks) == len(blocks)


def test_equal_length_turns_still_split_by_turn():
    from occ3_sglang.calibration_corpus import split_turns
    blocks = _corpus(rows=60)
    assert len({value.rows for value in blocks}) == 1
    fit_blocks, eval_blocks = split_turns(blocks)
    assert len(eval_blocks) == 4 and len(fit_blocks) == 8
    assert all(set(value.turn_id for value in partition).isdisjoint(
        {other.turn_id for other in (eval_blocks if partition is fit_blocks else fit_blocks)})
        for partition in (fit_blocks, eval_blocks))
    for partition in (fit_blocks, eval_blocks):
        assert sum(value.rows for value in partition) % 60 == 0


def test_duplicate_turn_ids_are_refused():
    from occ3_sglang.calibration_corpus import TurnBlock, ordered_blocks
    from occ3_sglang.latent_types import LatentError
    blocks = _corpus()
    duplicate = TurnBlock(turn_id=blocks[0].turn_id, hidden=blocks[1].hidden,
                          embeddings=blocks[1].embeddings)
    with pytest.raises(LatentError) as caught:
        ordered_blocks((blocks[0], duplicate))
    assert caught.value.code == "identity"


def test_turn_block_refuses_a_shape_mismatch():
    from occ3_sglang.calibration_corpus import TurnBlock
    from occ3_sglang.latent_types import LatentError
    hidden = torch.randn(8, 16)
    with pytest.raises(LatentError):
        TurnBlock(turn_id="turn-x", hidden=hidden, embeddings=torch.randn(8, 32))
    with pytest.raises(LatentError):
        TurnBlock(turn_id="turn x", hidden=hidden, embeddings=hidden.clone())
    with pytest.raises(LatentError):
        TurnBlock(turn_id="turn-x", hidden=hidden, embeddings=hidden.clone(),
                  reference_embeddings=torch.randn(4, 16))


def test_row_budget_is_a_supported_value_and_reported():
    from occ3_sglang.calibration_corpus import M_ROW_BUDGETS, evaluate_turn_disjoint
    from occ3_sglang.latent_types import LatentError
    assert M_ROW_BUDGETS == (512, 1024, 2048)
    with pytest.raises(LatentError) as caught:
        evaluate_turn_disjoint(_corpus(), row_budget=600)
    assert caught.value.code == "profile"
    report = evaluate_turn_disjoint(_corpus(), row_budget=1024)
    assert report["row_budget"] == 1024
    assert report["rows_used"] == 480 <= report["row_budget"]
    assert report["fit_turns"] == 8 and report["available_fit_turns"] == 8


def test_m_truncation_is_deterministic_and_takes_whole_turns():
    from occ3_sglang.calibration_corpus import select_calibration_rows, split_turns
    blocks = _corpus(rows=200, count=12)
    fit_blocks, _ = split_turns(blocks)
    first = select_calibration_rows(fit_blocks, 512)
    assert first[1] == 400
    assert [value.turn_id for value in first[0]] == [
        value.turn_id for value in fit_blocks[:2]]
    assert select_calibration_rows(list(reversed(fit_blocks)), 512) == first
    assert select_calibration_rows(fit_blocks[1:], 512) == select_calibration_rows(
        fit_blocks[1:], 512)
    assert select_calibration_rows(fit_blocks, 1024)[1] == 1000
    assert select_calibration_rows(fit_blocks, 2048)[1] == 1600
    for budget, rows in ((512, 400), (1024, 1000), (2048, 1600)):
        selected, used = select_calibration_rows(fit_blocks, budget)
        assert used == sum(value.rows for value in selected) == rows


def test_refuses_below_the_evaluation_turn_floor():
    from occ3_sglang.calibration_corpus import MIN_EVALUATION_TURNS, evaluate_turn_disjoint
    from occ3_sglang.latent_types import LatentError
    assert MIN_EVALUATION_TURNS == 4
    short = _corpus()[:6]
    with pytest.raises(LatentError) as caught:
        evaluate_turn_disjoint(short, row_budget=512)
    assert caught.value.code == "profile"


def test_fails_closed_on_a_corpus_without_signal():
    from occ3_sglang.calibration_corpus import evaluate_turn_disjoint
    from occ3_sglang.latent_types import LatentError
    with pytest.raises(LatentError) as caught:
        evaluate_turn_disjoint(_corpus(count=12, rows=80, independent=True), row_budget=512)
    assert caught.value.code == "numerical"


def test_evaluation_reports_every_required_field():
    from occ3_sglang.calibration_corpus import EVALUATION_KEYS, evaluate_turn_disjoint
    from occ3_sglang.statebridge_calibrated import CalibrationProvenance
    provenance = CalibrationProvenance(
        layer_index=77, model_fingerprint="sha256:" + "a" * 64, checkpoint_revision="rev-1",
        tokenizer_fingerprint="sha256:" + "b" * 64, embedding_id="glm53-tp8-vocab",
        capture_method_id="glm53.final-prenorm.capture.v1")
    report = evaluate_turn_disjoint(_corpus(), row_budget=512, provenance=provenance)
    assert set(report) == set(EVALUATION_KEYS)
    assert report["eval_turns"] == 4 and report["fit_turns"] == 8
    assert report["eval_cosine"] > 0.6, report["eval_cosine"]
    assert report["eval_r2"] > 0.10, report["eval_r2"]
    assert report["eval_relative_error"] < 1.0
    assert report["refit_sensitivity"] > 0.3, report["refit_sensitivity"]
    assert report["hidden_roll_sensitivity"] > 0.3
    assert report["hidden_permute_sensitivity"] > 0.3
    assert report["independent_reference_cosine"] is not None
    assert report["independent_reference_cosine"] < 0.5
    assert report["independent_reference_sensitivity"] > 0.3
    assert report["token_reference_cosine"] is not None and report["token_reference_cosine"] < 0.5
    assert len(report["corpus_digest"]) == 64 and len(report["artifact_digest"]) == 64
    assert report["separation"] == {}


def test_evaluation_is_deterministic_for_a_permuted_input():
    from occ3_sglang.calibration_corpus import evaluate_turn_disjoint
    forward = evaluate_turn_disjoint(_corpus(), row_budget=512)
    backward = evaluate_turn_disjoint(tuple(reversed(_corpus())), row_budget=512)
    assert forward == backward


def test_controls_separate_correct_from_shuffled_and_null():
    from occ3_sglang.calibration_corpus import CONTROL_KINDS, split_turns, score_controls
    artifact, target_norm = _artifact()
    _, eval_blocks = split_turns(_corpus())
    scores = score_controls(artifact, eval_blocks[0], target_norm=target_norm)
    assert set(scores) == set(CONTROL_KINDS)
    correct = scores["correct"]["cosine"]
    assert correct > 0.8
    for control in ("shuffled", "null", "wrong-sender", "reference-token-only"):
        assert scores[control]["attached"] == 1
        assert scores[control]["cosine"] < correct - 0.3, (control, scores[control])
    assert scores["shuffled"]["cosine"] < 0.5 and scores["null"]["cosine"] < 0.5


def test_no_attachment_has_no_score_and_reference_tokens_are_not_the_state():
    from occ3_sglang.calibration_corpus import (SOURCE_NONE, SOURCE_TOKEN, split_turns,
                                                score_controls)
    artifact, target_norm = _artifact()
    _, eval_blocks = split_turns(_corpus())
    scores = score_controls(artifact, eval_blocks[1], target_norm=target_norm)
    assert scores["no-attachment"] == {"kind": "no-attachment", "source": SOURCE_NONE,
                                      "rows": 0, "attached": 0, "cosine": None,
                                      "relative_error": None}
    assert scores["reference-token-only"]["source"] == SOURCE_TOKEN
    assert scores["reference-token-only"]["rows"] == eval_blocks[1].rows
    assert scores["correct"]["source"] == "hidden"


def test_shuffled_and_null_rows_are_mathematically_distinct():
    from occ3_sglang.calibration_corpus import control_rows
    block = _corpus()[0]
    _, correct = control_rows(block, "correct")
    _, shuffled = control_rows(block, "shuffled")
    _, null = control_rows(block, "null")
    _, other = control_rows(block, "wrong-sender")
    assert torch.equal(shuffled, torch.flip(block.hidden, [0]))
    assert torch.equal(torch.flip(shuffled, [0]), block.hidden)
    assert not torch.equal(shuffled, correct)
    assert float(null.abs().sum()) == 0.0
    assert torch.equal(other, block.wrong_sender_hidden)
    assert not torch.equal(other, correct)


def test_separation_math():
    from occ3_sglang.calibration_corpus import separation
    result = separation([1.0, 1.0, -1.0], [0.0, -1.0, -2.0])
    assert result["trials"] == 3
    assert result["delta_accuracy"] == pytest.approx(2.0 / 3.0)
    assert result["delta_margin"] == pytest.approx(1.0)


def test_candidate_separation_is_reported_and_validated():
    from occ3_sglang.calibration_corpus import evaluate_turn_disjoint, validate_evaluation
    from occ3_sglang.latent_types import LatentError
    report = evaluate_turn_disjoint(
        _corpus(), row_budget=512,
        candidate_scores={"correct": [1.0] * 8, "shuffled": [-1.0] * 8, "null": [-2.0] * 8})
    assert validate_evaluation(report)
    assert report["separation"]["shuffled"]["delta_accuracy"] == pytest.approx(1.0)
    assert report["separation"]["null"]["delta_margin"] == pytest.approx(3.0)
    with pytest.raises(LatentError):
        evaluate_turn_disjoint(_corpus(), row_budget=512, candidate_scores={"shuffled": [1.0]})
    with pytest.raises(LatentError):
        validate_evaluation(dict(report, separation={"shuffled": {"trials": 1}}))


def test_fit_transform_is_permutation_invariant_over_calibration_rows():
    """Re-ordering the calibration rows cannot change the frozen transform.

    The means, the Gram and the map are permutation invariant, so the only
    order-dependent quantity in a fit is its internal row-order held-out
    estimate. This is why re-fit sensitivity has to destroy the row
    correspondence to move the output at all.
    """
    from occ3_sglang.calibration_corpus import pooled_rows, select_calibration_rows, split_turns
    from occ3_sglang.statebridge_calibrated import apply_calibration, fit_calibration
    fit_blocks, _ = split_turns(_corpus())
    selected, _ = select_calibration_rows(fit_blocks, 512)
    hidden, embeddings = pooled_rows(selected)
    first = fit_calibration(hidden, embeddings)
    order = torch.arange(hidden.shape[0] - 1, -1, -1)
    second = fit_calibration(hidden.index_select(0, order), embeddings.index_select(0, order))
    inverse = torch.argsort(order)
    restored = second.gram.index_select(0, inverse).index_select(1, inverse)
    assert first.ridge == pytest.approx(second.ridge, rel=1e-5)
    assert torch.allclose(first.gram, restored, atol=1e-4)
    assert torch.allclose(first.mean_hidden, second.mean_hidden, atol=1e-6)
    message = hidden[:16]
    assert torch.allclose(apply_calibration(first, message, 2.0),
                          apply_calibration(second, message, 2.0), atol=1e-4)
