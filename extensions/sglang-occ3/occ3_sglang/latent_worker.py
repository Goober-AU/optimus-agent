"""Real rank-local CUDA worker helpers for the pinned-source overlays.

Import is inert. Installation is explicit after native model/group construction.
No tensor from this module is placed on a frontend IPC result.
"""
from __future__ import annotations

import hashlib
import time
from array import array
from dataclasses import asdict, dataclass
from typing import Any

from .attachment_contract import CaptureWorkerDescriptor, ConsumeWorkerDescriptor, digest
from .latent_registry import LatentRegistry
from .latent_types import (LatentError, UniformRequestCancellation, Publication, RequestSlice, RuntimeProfile,
                           SessionEnrollment, decode_descriptor, require,
                           validate_control_payload)
from .statebridge import (align_hidden_sequence, anchor_tiled, mean_embedding_norm,
                          shard_ranges, workspace_bound)

_RUNTIME = None


@dataclass(repr=False)
class CaptureRows:
    context: tuple
    hidden: Any
    positions: Any
    mode: str
    event: Any
    iteration: int


@dataclass(repr=False)
class CaptureStage:
    context: tuple
    hidden: Any
    tokens: Any
    positions: Any
    counts: Any
    event: Any
    iteration: int
    committed: bool = False


class WorkerRuntime:
    def __init__(self, model_runner, tp_group, profile, *, scheduler):
        import torch
        self.torch, self.runner, self.group, self.profile = torch, model_runner, tp_group, profile
        self.registry = LatentRegistry(profile)
        self.streams = {}
        self.poisoned = False
        self.commands = {}
        self.target_norm = None
        local_error = ""
        local_layout = None
        try:
            from sglang.srt.mem_cache.radix_cache import RadixCache
            require(scheduler is not None and type(scheduler.tree_cache) is RadixCache, "profile")
            kv_layout = validate_kv_layout(model_runner, scheduler, torch)
            config = model_runner.model_config.hf_config
            args = model_runner.server_args
            require(config.model_type == "glm_moe_dsa" and config.architectures == ["GlmMoeDsaForCausalLM"], "profile")
            require(config.hidden_size == profile.hidden_dim and config.num_hidden_layers == 78, "profile")
            require(config.quantization_config.get("quant_method") == "fp8", "profile")
            validate_target_model_class(model_runner.model)
            from sglang.srt.runtime_context import get_parallel
            parallel = get_parallel()
            require((parallel.tp_size, parallel.dp_size, parallel.pp_size, parallel.attn_cp_size,
                     parallel.attn_dcp_size, parallel.attn_tp_size) == (8, 1, 1, 1, 1, 8), "profile")
            require(parallel.tp_group is tp_group and parallel.pp_group.world_size == 1, "profile")
            require(getattr(args, "speculative_algorithm", None) in ("EAGLE", "NEXTN")
                    and getattr(args, "speculative_eagle_topk", None) == 1, "profile")
            require(not getattr(args, "enable_lora", False) and not getattr(args, "enable_dp_attention", False)
                    and getattr(args, "disaggregation_mode", "null") == "null", "profile")
            self.embedding = model_runner.model.get_input_embeddings()
            require(self.embedding.weight.is_cuda and self.embedding.weight.dtype in
                    (torch.float16, torch.bfloat16, torch.float32), "profile")
            require(torch.distributed.is_initialized(), "ranks")
            require(torch.distributed.get_world_size(tp_group.device_group) == profile.expected_ranks, "ranks")
            self.rank = torch.distributed.get_rank(tp_group.device_group)
            self.device = self.embedding.weight.device
            # Native schedule/forward/copy work can precede the model hook. Hold
            # actual initialized stream objects so cleanup fences those queues too.
            for stream in (scheduler.schedule_stream, scheduler.forward_stream,
                           scheduler.copy_stream, model_runner.forward_stream):
                require(stream is not None and stream.device == self.device, "profile")
                self.streams[stream.cuda_stream] = stream
            self.dtype = str(self.embedding.output_dtype or self.embedding.weight.dtype).split(".")[-1]
            require(self.dtype in ("float16", "bfloat16", "float32"), "profile")
            self.element_size = {"float16": 2, "bfloat16": 2, "float32": 4}[self.dtype]
            local_layout = {"rank": self.rank, "ranges": shard_ranges(self.embedding),
                            "embedding_tp": self.embedding.tp_size, "dim": profile.hidden_dim,
                            "vocab": self.embedding.num_embeddings, "dtype": self.dtype,
                            "kv": kv_layout}
        except Exception:
            local_error = "profile"
        errors = self.gather(local_error)
        require(not any(errors), "profile")
        layouts = self.gather(local_layout)
        require([x["rank"] for x in layouts] == list(range(profile.expected_ranks)), "ranks")
        require(all(x["dim"] == profile.hidden_dim and x["vocab"] == self.embedding.num_embeddings
                    and x["dtype"] == self.dtype and x["kv"] == local_layout["kv"] for x in layouts), "ranks")
        self.layout_digest = digest(layouts)
        self.consensus({"profile": profile.binding_digest, "layout": self.layout_digest})
        self.initialized = True

    def gather(self, value):
        result = [None] * self.profile.expected_ranks
        self.torch.distributed.all_gather_object(result, value, group=self.group.cpu_group)
        return result

    def consensus(self, value):
        values = self.gather(value)
        if not all(x == values[0] for x in values):
            self.poisoned = True
            raise LatentError("ranks")
        return value


    def preflight(self, phase, check, *, descriptor=None):
        """Every rank agrees on request/error before a safe cancel can be typed."""
        value, error = None, ""
        request_binding = None if descriptor is None else digest(asdict(descriptor))
        try:
            value = check()
        except Exception as exc:
            error = exc.code if isinstance(exc, LatentError) else "latent_failed"
        outcomes = self.gather({"phase": phase, "request": request_binding, "binding": value, "error": error})
        if any(item != outcomes[0] for item in outcomes):
            self.poisoned = True
            raise LatentError("ranks")
        if error:
            if (error in ("expired", "retired") and not self.poisoned
                    and type(descriptor) in (CaptureWorkerDescriptor, ConsumeWorkerDescriptor)):
                raise UniformRequestCancellation(error, descriptor.native_rid, descriptor.attempt_id)
            raise LatentError(error)
        return value

    def event(self):
        stream = self.torch.cuda.current_stream(self.device)
        self.streams[stream.cuda_stream] = stream
        event = self.torch.cuda.Event()
        event.record(stream)
        return event

    def fence(self):
        for stream in self.streams.values():
            event = self.torch.cuda.Event()
            event.record(stream)
            event.synchronize()

    def health(self):
        require(self.initialized and not self.poisoned, "unavailable")
        p = self.profile
        return {"runtime_epoch": p.runtime_epoch, "runtime_generation": p.runtime_generation,
                "model_fingerprint": p.model_fingerprint, "method_id": p.method,
                "expected_ranks": p.expected_ranks, "ready_ranks": p.expected_ranks,
                "tp_layout_digest": self.layout_digest, "hidden_dim": p.hidden_dim,
                "layer_index": p.layer_index, "dtype": self.dtype,
                "profile_digest": p.binding_digest, "source_capture": True,
                "continuous_prefix": True, "terminal_journal": True}

    def capture_budget(self, count):
        # Fixed ring + terminal ordered H/E/Z + stage/safe gather/dtype cast margin.
        # Small alignment solver workspace is separately reserved by profile.
        require(count in (8, 16, 32, 64, 128) and count <= self.profile.max_prefix_tokens, "profile")
        require(workspace_bound(count, self.profile.hidden_dim, self.profile.expected_ranks,
                                self.profile.tile_rows, self.profile.anchor_alpha)
                <= self.profile.workspace_bytes, "quota")
        return self.profile.expected_ranks * ((5 * count + 32) * self.profile.hidden_dim * self.element_size
                                             + (4 * count + 64) * 8)

    def control(self, envelope):
        command = validate_control_payload(envelope)
        command_id, op, payload = command["command_id"], command["operation"], command["payload"]
        fingerprint = digest(command)
        self.consensus(fingerprint)
        old = self.commands.get(command_id)
        if old is not None:
            require(old[0] == fingerprint, "replay")
            if op != "retire":
                return old[1]
        require(op in ("health", "status", "publication_status") or old is not None
                or len(self.commands) < self.profile.max_records * 16, "quota")
        require(not self.poisoned, "unavailable")
        result, error = {}, ""
        try:
            self.registry.reap()
            if op == "health":
                require(not payload)
                result = self.health()
            elif op == "enroll_session":
                self.registry.enroll(SessionEnrollment(**payload))
                result = self.health()
            elif op == "reserve_capture":
                require(set(payload) == {"descriptor", "source_handle", "state_generation", "expires_at_epoch"})
                descriptor = decode_descriptor(payload["descriptor"])
                charge = self.capture_budget(descriptor.max_prefix_tokens)
                record = self.registry.reserve_capture(descriptor, payload["source_handle"], payload["state_generation"],
                        payload["expires_at_epoch"], descriptor.max_capture_bytes, charge)
                result = record.snapshot()
            elif op == "reserve_consume":
                require(set(payload) == {"descriptor"})
                result = self.registry.reserve_consume(decode_descriptor(payload["descriptor"])).snapshot()
            elif op == "publish":
                result = self.registry.publish(Publication(**payload))
            elif op == "publication_status":
                require(set(payload) == {"publication_id", "principal_digest"})
                pub = self.registry.publications.get(payload["publication_id"])
                if pub is None:
                    result = {"state": "absent"}
                else:
                    require(pub.principal_digest == payload["principal_digest"], "identity")
                    result = self.registry.get(pub.source_handle, allow_retired=True).snapshot()
                    result["binding_digest"] = pub.binding_digest
            elif op == "status":
                require(set(payload) in ({"attempt_id", "principal_digest"}, {"source_handle", "principal_digest"}))
                record = (self.registry.source_for_attempt(payload["attempt_id"]) if "attempt_id" in payload
                          else self.registry.get(payload["source_handle"], allow_retired=True))
                require(record.descriptor.principal_digest == payload["principal_digest"], "identity")
                result = record.snapshot()
            elif op == "bind_output":
                require(set(payload) == {"attempt_id", "principal_digest", "binding"})
                record = self.registry.source_for_attempt(payload["attempt_id"])
                require(record.descriptor.principal_digest == payload["principal_digest"], "identity")
                result = self.registry.bind_output(payload["attempt_id"], payload["binding"])
            elif op == "retire":
                require(set(payload) in ({"attempt_id", "principal_digest"},
                                        {"source_handle", "source_session_id", "principal_digest"}))
                handle = payload.get("source_handle", self.registry.attempts.get(payload.get("attempt_id")))
                if handle in self.registry.records:
                    record = self.registry.get(handle, allow_retired=True)
                    require(record.descriptor.principal_digest == payload["principal_digest"], "identity")
                    if "source_session_id" in payload:
                        require(record.descriptor.source_session_id == payload["source_session_id"], "identity")
                result = self.registry.retire(handle=handle)
            else:
                raise LatentError("invalid")
        except Exception as exc:
            error = exc.code if isinstance(exc, LatentError) else "latent_failed"
        outcomes = self.gather({"error": error, "result": result})
        if any(item != outcomes[0] for item in outcomes):
            self.poisoned = True
            result, error = {}, "ranks"
        if error:
            result = {}
        reply = {"schema": "occ3.control.result.v1", "command_id": command_id,
                 "ok": not bool(error), "result": result, "error": error}
        validate_control_payload(reply, result=True)
        # Status/health must observe fresh journal/event state, not stale successful snapshots.
        if op not in ("health", "status", "publication_status"):
            self.commands[command_id] = (fingerprint, reply)
        return reply

    def resolve(self, descriptor, *, check_deadline=False):
        require(not self.poisoned, "unavailable")
        record = self.registry.source_for_attempt(descriptor.attempt_id)
        require(not record.revoked, "retired")
        if check_deadline:
            require(descriptor.deadline > time.monotonic() and record.expires_at_epoch > time.time(), "expired")
        expected = record.descriptor if type(descriptor) is CaptureWorkerDescriptor else record.consumer
        require(expected == descriptor, "identity")
        return record

    def align(self, record, req):
        torch = self.torch
        prepared = {}
        self.preflight("align_begin", lambda: _begin_gpu(self, record))
        def local_prepare():
            n = min(record.accepted_count, record.descriptor.max_prefix_tokens)
            require(n >= 2 and record.retained, "lineage")
            require(workspace_bound(n, self.profile.hidden_dim, self.profile.expected_ranks,
                                    self.profile.tile_rows, self.profile.anchor_alpha)
                    <= self.profile.workspace_bytes, "quota")
            ring_h, ring_t = record.retained
            cursor = record.accepted_count % ring_h.shape[0]
            if record.accepted_count < ring_h.shape[0]:
                order = torch.arange(n, device=self.device)
            else:
                order = (torch.arange(n, device=self.device) + cursor) % ring_h.shape[0]
            selected = ring_h.index_select(0, order)
            prepared.update(n=n, h=selected, tokens=ring_t.index_select(0, order), prefix=torch.empty_like(selected))
            return {"n": n, "count": record.accepted_count, "dtype": self.dtype}
        self.preflight("align_prepare", local_prepare)
        n, h, tokens = prepared["n"], prepared["h"], prepared["tokens"]
        embeddings = self.embedding(tokens)
        if self.target_norm is None:
            self.target_norm = mean_embedding_norm(self.embedding, tp_group=self.group,
                                                  tile_rows=self.profile.tile_rows)
        # All ranks reach the outcome handshake even if the root solver fails.
        solver_error, prefix = "", prepared["prefix"]
        if self.rank == 0:
            try:
                aligned, _ = align_hidden_sequence(h, embeddings, self.target_norm)
                prefix.copy_(aligned)
            except Exception:
                solver_error = "numerical"
        errors = self.gather(solver_error)
        require(not any(errors), "numerical")
        torch.distributed.broadcast(prefix, src=self.group.ranks[0], group=self.group.device_group)
        prefix = anchor_tiled(prefix, self.embedding, alpha=self.profile.anchor_alpha,
                              tp_group=self.group, tile_rows=self.profile.tile_rows)
        event = self.event()
        record.prefix = prefix
        record.events.append(event)
        record.live_refs.append((event, (h, tokens, embeddings)))
        end = record.accepted_count
        start = end - n
        sequence_digest = digest(list(req.output_ids[start:end]))
        commit = digest([record.descriptor.reservation_id, start, end, sequence_digest, self.layout_digest])
        metadata = {"capture_commit_id": commit, "accepted_span_start": start,
                    "accepted_span_end": end, "retained_count": n,
                    "predictor_position_offset": -1, "representation": "final_layer_prenorm_predictor",
                    "layer_index": self.profile.layer_index, "hidden_dim": self.profile.hidden_dim,
                    "dtype": self.dtype, "layout_digest": self.layout_digest,
                    "ready_ranks": self.profile.expected_ranks, "expected_ranks": self.profile.expected_ranks,
                    "accepted_sequence_digest": sequence_digest, "expires_at_epoch": record.expires_at_epoch,
                    "byte_count": prefix.numel() * prefix.element_size()}
        self.preflight("source_ready", lambda: event.synchronize())
        self.consensus(metadata)
        self.preflight("align_complete", lambda: _end_gpu(self, record))
        record.capture_commit_id, record.source_metadata, record.state = commit, metadata, "ready"
        # Retained ring no longer needed, but copies remain event-owned until complete.
        record.retained.clear()
        self.reap_live(record)

    def reap_live(self, record):
        record.live_refs[:] = [(event, refs) for event, refs in record.live_refs if not event.query()]
        record.events[:] = [event for event in record.events if not event.query()]


def install_worker(model_runner, tp_group, profile, *, scheduler=None):
    global _RUNTIME
    require(_RUNTIME is None, "state")
    runtime = WorkerRuntime(model_runner, tp_group, profile, scheduler=scheduler)
    _RUNTIME = runtime
    if scheduler is not None:
        scheduler.occ3_runtime = runtime
    return runtime


def _runtime(scheduler=None):
    runtime = getattr(scheduler, "occ3_runtime", None) if scheduler is not None else _RUNTIME
    require(runtime is not None and runtime is _RUNTIME and not runtime.poisoned, "unavailable")
    return runtime


def handle_control(scheduler, payload):
    return _runtime(scheduler).control(payload)


def deliver_control(tokenizer_manager, payload):
    from .latent_backend import deliver_control as deliver
    return deliver(tokenizer_manager, payload)


def batch_context(reqs, extend_lens=None, prefix_lens=None, final_chunks=None):
    descriptors = [getattr(req, "occ3_worker_descriptor", None) for req in reqs]
    if not any(d is not None for d in descriptors):
        return None
    require(all(d is not None for d in descriptors) and len(reqs) == 1, "profile")
    output, offset = [], 0
    for i, (req, descriptor) in enumerate(zip(reqs, descriptors)):
        length = int(extend_lens[i]) if extend_lens is not None else 0
        prefix = int(prefix_lens[i]) if prefix_lens is not None else 0
        final = bool(final_chunks[i]) if final_chunks is not None else False
        output.append(RequestSlice(descriptor, offset, length, prefix, len(req.output_ids), final))
        offset += length
    return tuple(output)


def prepare_batch(scheduler, batch):
    if not any(getattr(req, "occ3_worker_descriptor", None) is not None for req in batch.reqs):
        batch.occ3_context, batch.occ3_force_eager = None, False
        return
    runtime = _runtime(scheduler)
    prepared = {}
    def validate():
        lengths = getattr(batch, "extend_lens", None) if batch.forward_mode.is_extend() else None
        prefixes = getattr(batch, "prefix_lens", None) if lengths is not None else None
        finals = [r.extend_range.end >= len(r.full_untruncated_fill_ids) for r in batch.reqs] if lengths is not None else None
        context = batch_context(batch.reqs, lengths, prefixes, finals)
        for item in context:
            runtime.resolve(item.descriptor, check_deadline=True)
        prepared["context"] = context
        return digest([asdict(item) for item in context])
    runtime.preflight("prepare", validate, descriptor=batch.reqs[0].occ3_worker_descriptor)
    batch.occ3_context, batch.occ3_force_eager = prepared["context"], True


def admit_request(scheduler, req, value):
    if value is None:
        return
    runtime = _runtime(scheduler)
    prepared = {}
    def validate():
        descriptor = decode_descriptor(value)
        require(req.rid == descriptor.native_rid and not req.output_ids, "identity")
        record = runtime.resolve(descriptor, check_deadline=True)
        if type(descriptor) is CaptureWorkerDescriptor:
            require(record.state == "reserved" and not record.admitted_capture, "replay")
        else:
            require(record.state == "leased" and not record.admitted_consume, "replay")
        require(getattr(req, "session", None) is None and getattr(req, "multimodal_inputs", None) is None, "profile")
        require(getattr(req, "positional_embed_overrides", None) is None, "profile")
        require(getattr(req, "occ3_worker_descriptor", None) is None, "replay")
        original = req.origin_input_ids
        new_ids = original
        if type(descriptor) is ConsumeWorkerDescriptor:
            count = descriptor.expected_prefix_tokens
            new_ids = array(original.typecode, [0] * count) + original if isinstance(original, array) else [0] * count + list(original)
        prepared.update(descriptor=descriptor, record=record, new_ids=new_ids)
        return {"descriptor": digest(asdict(descriptor)), "tokens": digest(list(original)),
                "state": record.state, "generation": record.state_generation}
    runtime.preflight("admit", validate)
    descriptor, record, new_ids = prepared["descriptor"], prepared["record"], prepared["new_ids"]
    req.occ3_worker_descriptor, req.occ3_force_eager = descriptor, True
    if type(descriptor) is CaptureWorkerDescriptor:
        record.admitted_capture = True
        record.state = "capturing"
    else:
        record.admitted_consume = True
        req.origin_input_ids = new_ids
        req.origin_input_ids_unpadded = new_ids[:]
        req.full_untruncated_fill_ids = (new_ids + array(new_ids.typecode, req.output_ids)
            if isinstance(new_ids, array) else new_ids + list(req.output_ids))
        req.skip_radix_cache_insert, req.occ3_private_kv = True, True
        req.occ3_private_prefix = True


def _context(forward_batch):
    context = getattr(forward_batch, "occ3_context", None)
    if not context:
        return None
    require(getattr(forward_batch, "occ3_force_eager", False) and len(context) == 1, "profile")
    require(getattr(forward_batch, "batch_size", 1) == 1, "profile")
    return context


def capture_prenorm(forward_batch, hidden_states):
    context = _context(forward_batch)
    if not context or type(context[0].descriptor) is not CaptureWorkerDescriptor:
        return
    runtime = _runtime()
    prepared = {}
    def capture():
        record = runtime.resolve(context[0].descriptor)
        _begin_gpu(runtime, record)
        runtime.reap_live(record)
        torch = runtime.torch
        require(hidden_states.is_cuda and hidden_states.ndim == 2
                and hidden_states.shape[1] == runtime.profile.hidden_dim, "profile")
        require(str(hidden_states.dtype).split(".")[-1] == runtime.dtype, "profile")
        if forward_batch.forward_mode.is_target_verify():
            require(1 <= hidden_states.shape[0] <= 6, "profile")
            rows = torch.arange(hidden_states.shape[0], device=hidden_states.device)
            mode = "verify"
        elif forward_batch.forward_mode.is_extend():
            item = context[0]
            require(item.extend_len > 0, "profile")
            rows = torch.tensor([item.extend_start + item.extend_len - 1], device=hidden_states.device)
            mode = "prefill"
        else:
            raise LatentError("profile")
        copied = hidden_states.index_select(0, rows).clone()
        positions = forward_batch.positions.index_select(0, rows).clone()
        event = runtime.event()
        record.events.append(event)
        record.live_refs.append((event, (copied, positions)))
        prepared["rows"] = CaptureRows(context, copied, positions, mode, event,
                                       int(getattr(forward_batch, "occ3_forward_iter", 0)))
        _end_gpu(runtime, record)
        return {"mode": mode, "rows": copied.shape[0], "dim": copied.shape[1]}
    runtime.preflight("capture_prenorm", capture)
    forward_batch.occ3_capture_rows = prepared["rows"]


def stage_prefill(batch, logits_output, next_token_ids):
    context = getattr(batch, "occ3_context", None)
    if not context or type(context[0].descriptor) is not CaptureWorkerDescriptor:
        return None
    runtime = _runtime()
    prepared = {}
    def stage():
        rows = getattr(logits_output, "occ3_capture_rows", None)
        require(rows is not None and rows.mode == "prefill" and rows.context == context, "lineage")
        if not rows.context[0].final_chunk:
            prepared["stage"] = None
            return {"middle_chunk": True}
        torch = runtime.torch
        record = runtime.resolve(rows.context[0].descriptor)
        _begin_gpu(runtime, record)
        torch.cuda.current_stream(runtime.device).wait_event(rows.event)
        tokens = next_token_ids.reshape(-1)[:1].clone().reshape(1, 1)
        event = runtime.event()
        record.events.append(event)
        record.live_refs.append((event, (rows, tokens)))
        prepared["stage"] = CaptureStage(rows.context, rows.hidden.reshape(1, 1, -1), tokens,
            rows.positions.reshape(1, 1), torch.ones(1, device=runtime.device, dtype=torch.int64), event, rows.iteration)
        _end_gpu(runtime, record)
        return {"middle_chunk": False, "iteration": rows.iteration}
    runtime.preflight("stage_prefill", stage)
    return prepared["stage"]


def stage_verify(forward_batch, logits_output, predict, accept_lens, accept_index, positions):
    context = getattr(forward_batch, "occ3_context", None)
    if not context or type(context[0].descriptor) is not CaptureWorkerDescriptor:
        return None
    runtime = _runtime()
    prepared = {}
    def stage():
        rows = getattr(logits_output, "occ3_capture_rows", None)
        require(rows is not None and rows.mode == "verify" and rows.context == context, "lineage")
        record = runtime.resolve(rows.context[0].descriptor)
        _begin_gpu(runtime, record)
        torch = runtime.torch
        torch.cuda.current_stream(runtime.device).wait_event(rows.event)
        require(accept_index.ndim == 2 and accept_index.shape[0] == 1 and accept_index.shape[1] <= 6, "profile")
        count = accept_lens.reshape(-1)
        valid = torch.arange(accept_index.shape[1], device=runtime.device)[None, :] < count[:, None]
        require(bool((((accept_index >= 0) & (accept_index < rows.hidden.shape[0])) | ~valid).all()), "lineage")
        require(bool(((count > 0) & (count <= accept_index.shape[1])).all()), "lineage")
        indices = torch.where(valid, accept_index, torch.zeros_like(accept_index)).reshape(-1).long()
        hidden = rows.hidden.index_select(0, indices).reshape(1, -1, runtime.profile.hidden_dim).clone()
        tokens = predict.reshape(-1).index_select(0, indices).reshape(1, -1).clone()
        selected_positions = positions.reshape(-1).index_select(0, indices).reshape(1, -1).clone()
        event = runtime.event()
        record.events.append(event)
        record.live_refs.append((event, (rows, hidden, tokens, selected_positions)))
        prepared["stage"] = CaptureStage(rows.context, hidden, tokens, selected_positions, count.clone(), event, rows.iteration)
        _end_gpu(runtime, record)
        return {"count": int(count[0]), "width": accept_index.shape[1], "iteration": rows.iteration}
    runtime.preflight("stage_verify", stage)
    return prepared["stage"]


def prefix_overlap(prefix_tokens, prefix_len, extend_len, shift=0):
    require(all(type(x) is int and x >= 0 for x in (prefix_tokens, prefix_len, extend_len, shift)))
    return max(0, min(extend_len, prefix_tokens - prefix_len - shift))


def _effective(model_runner, forward_batch, shift):
    """Draft-only fresh native embedding replacement, no clone or long-lived ref."""
    context = _context(forward_batch)
    if not context or type(context[0].descriptor) is not ConsumeWorkerDescriptor:
        return None
    runtime = _runtime()
    item, prepared = context[0], {}
    def validate():
        record = runtime.resolve(item.descriptor)
        require(record.prefix is not None and item.extend_len > 0, "not_ready")
        require(forward_batch.input_ids.numel() == item.extend_len, "profile")
        require(record.prefix.is_cuda and record.prefix.shape ==
                (item.descriptor.expected_prefix_tokens, runtime.profile.hidden_dim), "profile")
        prepared["record"] = record
        prepared["count"] = prefix_overlap(record.prefix.shape[0], item.prefix_len, item.extend_len, shift)
        return {"start": item.prefix_len + shift, "length": item.extend_len, "count": prepared["count"],
                "descriptor": digest(asdict(item.descriptor))}
    runtime.preflight("draft_embedding_prepare", validate)
    record, count = prepared["record"], prepared["count"]
    if not count:
        return None  # Native NextN performs its ordinary lookup outside prefix.
    runtime.preflight("draft_embedding_begin", lambda: _begin_gpu(runtime, record))
    for event in record.events:
        runtime.torch.cuda.current_stream(runtime.device).wait_event(event)
    embedding = model_runner.model.get_input_embeddings()
    values = embedding(forward_batch.input_ids)
    def replace():
        require(values.is_cuda and values.shape == (item.extend_len, runtime.profile.hidden_dim)
                and values.dtype == record.prefix.dtype, "profile")
        start = item.prefix_len + shift
        values[:count].copy_(record.prefix[start:start + count])
        values.record_stream(runtime.torch.cuda.current_stream(runtime.device))
        record.events.append(runtime.event())
        return {"count": count, "dtype": str(values.dtype)}
    runtime.preflight("draft_embedding_replace", replace)
    runtime.preflight("draft_embedding_complete", lambda: _end_gpu(runtime, record))
    # NextN already keeps its normal embedding through the forward. Do not add
    # a second clone, target sidecar or registry reference to this full chunk.
    return values


def target_input_embeds(model_runner, forward_batch):
    """Validation-only seam. Native inner model owns the embedding allocation."""
    context = _context(forward_batch)
    if context is not None:
        require(model_runner.is_draft_worker is False, "profile")
    return None


def inject_target_embeddings(forward_batch, hidden_states):
    """Scatter at the native lookup site; never retain the full hidden buffer."""
    context = _context(forward_batch)
    if not context or type(context[0].descriptor) is not ConsumeWorkerDescriptor:
        return
    if not forward_batch.forward_mode.is_extend():
        return
    runtime = _runtime()
    item, prepared = context[0], {}
    def validate_and_copy():
        from sglang.srt.layers.layer_boundary import get_attn_tp_context
        require(not get_attn_tp_context().input_scattered, "profile")
        record = runtime.resolve(item.descriptor)
        _begin_gpu(runtime, record)
        count = prefix_overlap(item.descriptor.expected_prefix_tokens, item.prefix_len, item.extend_len)
        require(hidden_states.is_cuda and hidden_states.shape == (item.extend_len, runtime.profile.hidden_dim)
                and hidden_states.dtype == record.prefix.dtype, "profile")
        if count:
            for event in record.events:
                runtime.torch.cuda.current_stream(runtime.device).wait_event(event)
            hidden_states[:count].copy_(record.prefix[item.prefix_len:item.prefix_len + count])
            hidden_states.record_stream(runtime.torch.cuda.current_stream(runtime.device))
            record.events.append(runtime.event())
            # Two integers only. Keeping hidden_states here would pin a 384MiB
            # native 32768-row input across every decoder layer and TP rank.
            prepared["span"] = (item.prefix_len, count)
        else:
            prepared["span"] = None
        _end_gpu(runtime, record)
        return {"count": count, "start": item.prefix_len, "dtype": str(hidden_states.dtype)}
    runtime.preflight("target_embedding_inplace", validate_and_copy)
    forward_batch.occ3_effective_embeds = prepared["span"]


def draft_input_embeds(model_runner, forward_batch, batch, tail_tokens):
    if not getattr(forward_batch, "occ3_context", None):
        return None
    context = _context(forward_batch)
    if type(context[0].descriptor) is not ConsumeWorkerDescriptor:
        return None
    require(tail_tokens.numel() == 1 and context == getattr(batch, "occ3_context", None), "lineage")
    require(bool(forward_batch.input_ids[-1] == tail_tokens.reshape(-1)[0]), "lineage")
    return _effective(model_runner, forward_batch, 1)


def mark_injection(forward_batch):
    span = getattr(forward_batch, "occ3_effective_embeds", None)
    if span is None:
        return
    context = _context(forward_batch)
    runtime = _runtime()
    item = context[0]
    record = runtime.resolve(item.descriptor)
    require(type(span) is tuple and len(span) == 2 and all(type(x) is int for x in span), "state")
    start, count = span
    require(start == item.prefix_len and count == prefix_overlap(record.prefix.shape[0], start, item.extend_len), "lineage")
    record.injected_positions.update(range(start, start + count))
    record.events.append(runtime.event())
    forward_batch.occ3_effective_embeds = None
    if len(record.injected_positions) == record.prefix.shape[0]:
        record.injection_commit_id = digest([item.descriptor.reservation_id, record.capture_commit_id,
                                             sorted(record.injected_positions)])
        record.state = "injected"


def commit_result(scheduler, req, result, request_index, output_start, output_end, *, terminal=False, forward_iter):
    descriptor = getattr(req, "occ3_worker_descriptor", None)
    if descriptor is None:
        return
    runtime = _runtime(scheduler)
    torch = runtime.torch
    prepared = {}
    def validate():
        record = runtime.resolve(descriptor)
        require(type(forward_iter) is int and forward_iter >= 0, "lineage")
        require(getattr(req, "retraction_count", 0) == 0
                and 0 <= output_start <= output_end <= len(req.output_ids), "lineage")
        prepared["record"] = record
        if type(descriptor) is CaptureWorkerDescriptor:
            stage = getattr(result, "occ3_capture_stage", None)
            require(stage is not None and not stage.committed and request_index == 0
                    and stage.iteration == forward_iter, "lineage")
            item = stage.context[request_index]
            require(item.descriptor == descriptor and item.output_start == output_start == record.accepted_count, "lineage")
            _begin_gpu(runtime, record)
            torch.cuda.current_stream(runtime.device).wait_event(stage.event)
            count = output_end - output_start
            require(0 < count <= int(stage.counts[request_index]), "lineage")
            actual = torch.tensor(list(req.output_ids[output_start:output_end]), device=runtime.device, dtype=stage.tokens.dtype)
            positions = torch.arange(len(req.origin_input_ids) + output_start - 1,
                                     len(req.origin_input_ids) + output_end - 1, device=runtime.device)
            require(bool(torch.equal(stage.tokens[request_index, :count], actual)) and
                    bool(torch.equal(stage.positions[request_index, :count], positions)), "lineage")
            prepared.update(stage=stage, count=count, actual=actual)
            _end_gpu(runtime, record)
        elif terminal:
            require(req.skip_radix_cache_insert and getattr(req, "occ3_private_kv", False)
                    and getattr(req, "occ3_private_prefix", False), "profile")
            require(record.injection_commit_id is not None and
                    len(record.injected_positions) == descriptor.expected_prefix_tokens, "lineage")
        return {"descriptor": digest(asdict(descriptor)), "start": output_start, "end": output_end,
                "iteration": forward_iter,
                "visible_digest": digest(list(req.output_ids[output_start:output_end])), "terminal": bool(terminal)}
    runtime.preflight("commit_lineage", validate, descriptor=descriptor)
    record = prepared["record"]
    if type(descriptor) is CaptureWorkerDescriptor:
        stage, count, actual = prepared["stage"], prepared["count"], prepared["actual"]
        def append_ring():
            _begin_gpu(runtime, record)
            if not record.retained:
                record.retained = [torch.empty((descriptor.max_prefix_tokens, runtime.profile.hidden_dim),
                                               device=runtime.device, dtype=stage.hidden.dtype),
                                   torch.empty(descriptor.max_prefix_tokens, device=runtime.device, dtype=stage.tokens.dtype)]
            rh, rt = record.retained
            for i in range(count):
                destination = (record.accepted_count + i) % descriptor.max_prefix_tokens
                rh[destination].copy_(stage.hidden[request_index, i])
                rt[destination].copy_(stage.tokens[request_index, i])
            event = runtime.event()
            record.events.append(event)
            record.live_refs.append((event, (stage, actual)))
            _end_gpu(runtime, record)
            return {"count": count, "end": output_end}
        try:
            runtime.preflight("commit_write", append_ring)
        except Exception:
            runtime.poisoned = True
            raise
        record.accepted_count += count
        stage.committed = True
        if terminal:
            runtime.align(record, req)
    elif terminal:
        runtime.preflight("terminal_fence", lambda: runtime.fence())
        metadata = {"native_rid": req.rid, "injection_commit_id": record.injection_commit_id,
                    "injected_prefix_count": len(record.injected_positions), "ready_ranks": runtime.profile.expected_ranks,
                    "expected_ranks": runtime.profile.expected_ranks, "num_retractions": 0,
                    "private_cache_namespace": True, "gpu_resident": bool(record.prefix.is_cuda), "completed": True}
        runtime.consensus(metadata)
        record.terminal_metadata, record.state = metadata, "terminal"
    runtime.reap_live(record)


def _cleanup_runtime(scheduler):
    runtime = getattr(scheduler, "occ3_runtime", None)
    require(runtime is not None and runtime is _RUNTIME, "unavailable")
    return runtime


def fence_private_kv_release(scheduler, req):
    """Cleanup-only: no readiness/expiry gate may bypass a real KV stream fence."""
    runtime = _cleanup_runtime(scheduler)
    descriptor = getattr(req, "occ3_worker_descriptor", None)
    prepared = {}
    def fence_owned():
        require(type(descriptor) is ConsumeWorkerDescriptor and req.rid == descriptor.native_rid, "identity")
        record = runtime.registry.source_for_attempt(descriptor.attempt_id)
        require(record.consumer == descriptor, "identity")
        prepared["record"] = record
        runtime.fence()
        return {"reservation": descriptor.reservation_id, "rid": req.rid}
    try:
        runtime.preflight("private_kv_fence", fence_owned)
        prepared["record"].inflight = 0
    except Exception:
        runtime.poisoned = True
        raise


def abort_request(scheduler, req, reason):
    descriptor = getattr(req, "occ3_worker_descriptor", None)
    if descriptor is None:
        return
    runtime = _cleanup_runtime(scheduler)
    prepared = {}
    def fence_owned():
        record = runtime.registry.source_for_attempt(descriptor.attempt_id)
        prepared["record"] = record
        expected = record.descriptor if type(descriptor) is CaptureWorkerDescriptor else record.consumer
        require(expected == descriptor and req.rid == descriptor.native_rid, "identity")
        runtime.fence()
        return {"reservation": descriptor.reservation_id, "rid": req.rid}
    try:
        runtime.preflight("abort_fence", fence_owned)
        prepared["record"].inflight = 0
        runtime.registry.retire(attempt_id=descriptor.attempt_id)
    except Exception:
        runtime.poisoned = True
        raise


def load_runtime_profile():
    from .native_profile import get_runtime_incarnation, load_pinned_profile
    static, incarnation = load_pinned_profile(), get_runtime_incarnation()
    expected = set(RuntimeProfile.__dataclass_fields__) - {"runtime_epoch", "runtime_generation"}
    require(type(static) is dict and set(static) == expected, "profile")
    return RuntimeProfile(**static, runtime_epoch=incarnation["runtime_epoch"],
                          runtime_generation=incarnation["runtime_generation"])


def bootstrap_worker(scheduler):
    """Only the trusted ordered internal-control path may initialize this worker."""
    import os
    import torch
    global _RUNTIME
    group = scheduler.tp_group
    size = torch.distributed.get_world_size(group.cpu_group)
    require(size == 8, "profile")
    states = [None] * size
    torch.distributed.all_gather_object(states, {"installed": _RUNTIME is not None, "pid": os.getpid()}, group=group.cpu_group)
    installed = [item["installed"] for item in states]
    members = tuple(item["pid"] for item in states)
    if any(installed):
        if not all(installed) or _RUNTIME is None or _RUNTIME.members != members:
            if _RUNTIME is not None:
                _RUNTIME.poisoned = True
            raise LatentError("ranks")
        require(not _RUNTIME.poisoned and getattr(scheduler, "occ3_runtime", None) is _RUNTIME, "unavailable")
        return _RUNTIME
    profile, error = None, ""
    try:
        profile = load_runtime_profile()
    except Exception:
        error = "profile"
    profiles = [None] * size
    torch.distributed.all_gather_object(profiles, error or profile.binding_digest, group=group.cpu_group)
    require(not error and all(p == profiles[0] for p in profiles) and profiles[0] != "profile", "profile")
    runtime = install_worker(scheduler.tp_worker.model_runner, group, profile, scheduler=scheduler)
    runtime.members = members
    return runtime


def record_forward_completion(model_runner, forward_batch):
    context = _context(forward_batch)
    if context is None:
        return
    runtime = _runtime()
    runtime.preflight("forward_complete", lambda: _end_gpu(runtime, runtime.resolve(context[0].descriptor)))


def record_forward_start(model_runner, forward_batch):
    if not getattr(forward_batch, "occ3_context", None):
        return
    runtime = _runtime()
    prepared = {}
    def validate():
        context = _context(forward_batch)
        runtime.event()  # Track native eager-prepare submissions even on expiry.
        record = runtime.resolve(context[0].descriptor, check_deadline=True)
        prepared["record"] = record
        return {"context": digest([asdict(item) for item in context]),
                "mode": int(forward_batch.forward_mode),
                "draft": bool(getattr(model_runner, "is_draft_worker", False))}
    runtime.preflight("forward_start", validate, descriptor=forward_batch.occ3_context[0].descriptor)
    # An incomplete call cannot be reaped from its already-complete start event.
    runtime.preflight("forward_submit", lambda: _begin_gpu(runtime, prepared["record"]))


def validate_target_model_class(model):
    from sglang.srt.models.glm4_moe import GlmMoeDsaForCausalLM
    require(type(model) is GlmMoeDsaForCausalLM, "profile")


def validate_kv_layout(model_runner, scheduler, torch):
    """Observe initialized target storage; no inferred dtype or allocation."""
    from sglang.srt.mem_cache.memory_pool import DSATokenToKVPool
    pool = model_runner.token_to_kv_pool
    require(type(pool) is DSATokenToKVPool, "profile")
    require(scheduler.token_to_kv_pool_allocator is model_runner.token_to_kv_pool_allocator
            and scheduler.token_to_kv_pool_allocator.get_kvcache() is pool, "profile")
    require(model_runner.kv_cache_dtype_str == "fp8_e4m3"
            and model_runner.kv_cache_dtype == torch.float8_e4m3fn
            and pool.dtype == torch.float8_e4m3fn and pool.store_dtype == torch.uint8, "profile")
    require(pool.use_dsa is True and pool.page_size == 64 and pool.kernel_page_blocks == 1
            and pool.layer_num == 78 and pool.start_layer == 0 and pool.end_layer == 77
            and pool.layer_shard_enabled is False and pool.post_capture_active is False, "profile")
    config = model_runner.model_config.hf_config
    require(pool.kv_lora_rank == config.kv_lora_rank
            and pool.qk_rope_head_dim == config.qk_rope_head_dim
            and pool.index_head_dim == 128, "profile")
    require(type(pool.size) is int and pool.size > 0 and type(pool.kv_cache_dim) is int
            and pool.kv_cache_dim > 0 and len(pool.kv_buffer) == 78, "profile")
    geometry = (pool.size + pool.page_size, 1, pool.kv_cache_dim)
    require(all(buffer.is_cuda and buffer.dtype == pool.store_dtype and tuple(buffer.shape) == geometry
                and buffer.is_contiguous() for buffer in pool.kv_buffer), "profile")
    # FP8 is stored as uint8 by the native KVCache base. DSA may include packed
    # scale/BF16-rope bytes; do not mislabel physical uint8 as a dtype mismatch.
    return {"class": "DSATokenToKVPool", "semantic_dtype": "float8_e4m3fn",
            "storage_dtype": "uint8", "page_size": pool.page_size,
            "shape": list(geometry), "layers": pool.layer_num,
            "kv_lora_rank": pool.kv_lora_rank, "qk_rope_head_dim": pool.qk_rope_head_dim,
            "dsa_packed_fp8": bool(pool.dsa_kv_cache_store_fp8),
            "index_head_dim": pool.index_head_dim,
            "index_kpool": pool.index_kpool, "index_kpool_compress": bool(pool.index_kpool_compress)}


def _begin_gpu(runtime, record):
    record.inflight = getattr(record, "inflight", 0) + 1
    record.events.append(runtime.event())  # Registers current stream before submission.


def _end_gpu(runtime, record):
    require(getattr(record, "inflight", 0) > 0, "state")
    record.events.append(runtime.event())  # End event must exist before allowing reap.
    record.inflight -= 1
