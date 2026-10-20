# PUSH CHECKLIST - context rot iteration 2 (integrator only)

Every command below is for the INTEGRATOR, run from the integration worktree
`C:/Users/openclawuser/optimus-rot2-20261020` (branch
`improve/context-rot-iteration-2`). The PR body was assembled at `90fcd17db`
(143 commits over base `6000d6bc1`); the docs-only waves that follow it (the
W61 PR assembly and any later report/gate/matrix commits) are expected on top. No worker ever pushes; this campaign's
workers have no push rights on `telemusai/optimus-agent` and the fork is the
integrator's. The `gh` CLI is not installed on the campaign host (verified);
run the PR-open step from a machine with `gh` authenticated to the fork
account, or use the web fallback at the end.

## 0. Preconditions (verify, do not fix)

- [ ] `git -C C:/Users/openclawuser/optimus-rot2-20261020 status` is clean and
      the head descends from the assembly tip `90fcd17db`, with
      `git log --oneline 90fcd17db..HEAD` - after 90fcd17db the D33/D34 fixes, the clippy cleanup, test pins and doc passes landed; all are disclosed in the PR body. Confirm nothing NEW appeared beyond the intended set.
- [ ] `BUG-LEDGER.md` carries no VERIFIED-OPEN row except the documented open
      set (D7 deferred, D10 "never retry" sub-item, D12 soft-fence residual,
      D17 production wiring follow-up, D20 residue) and D34 (the W63 battery
      regression, fix in flight on W71). D33 landed (W54, `cea3a4496` + pin
      `6d85cc48f` + ledger row `510fd168d`): its ledger row, the PR body's D33
      row and the fragment bullet are flipped, as this checklist prescribed.
- [ ] `git lfs ls-files --size` lists exactly five raw-call logs, all with
      real sizes (not 130-byte pointers):
      `research/context_rot_r2/live/raw-calls.jsonl` (~8.1 MB),
      `live/e3/raw-calls.jsonl` (~2.4 MB),
      `live/e3adv/raw-calls.jsonl` (~1.4 MB),
      `live/e5/live/e3-typed-raw-calls.jsonl` (~2.5 MB),
      `compactbench_adapter/live/raw-calls.jsonl` (~262 KB).
- [ ] `git remote -v` shows `fork https://github.com/Goober-AU/optimus-agent.git`.

## 1. Push the branch to the fork

```
git push fork improve/context-rot-iteration-2
```

Fast-forward only; never force-push (the branch is linear over the base).

## 2. Push the LFS objects (must accompany step 1 or the pointers dangle)

```
# (LFS removed: the raw-call logs are now regular git objects after the fork LFS upload was unavailable)
```

The five raw-call logs were converted to LFS pointers by the W45 audit wave
(`fa3634b7a` re-staged them via a scoped `git add --renormalize`); the
pre-pointer blobs remain in earlier history, so the one large blob exists in
history once and every future write uses pointers. If the maintainers prefer
no LFS dependency, the documented alternative is to delete the five files
and the `.gitattributes` block in one commit instead (REVIEW-AUDIT.md S5).

## 3. Verify the pointers resolve on the remote

- [ ] `git lfs ls-files --size` unchanged after the push (local view).
- [ ] Web fallback: open
      `https://github.com/Goober-AU/optimus-agent/blob/improve/context-rot-iteration-2/research/context_rot_r2/live/raw-calls.jsonl`
      and confirm the file renders as JSONL (a dangling pointer renders as a
      ~130-byte `version https://git-lfs.github.com/spec/v1` stub).

## 4. Open the PR

With `gh` (authenticated to the fork account):

```
gh pr create --repo telemusai/optimus-agent \
  --base main \
  --head Goober-AU:improve/context-rot-iteration-2 \
  --title "Context rot iteration 2: compaction defect fixes, production-path benchmarks, typed-state v2 wired as an opt-in mode" \
  --body-file reports/context-rot-r2/PR-BODY.md
```

Web fallback (same content):

- URL: `https://github.com/telemusai/optimus-agent/compare/main...Goober-AU:optimus-agent:improve/context-rot-iteration-2`
- Title: `Context rot iteration 2: compaction defect fixes, production-path benchmarks, typed-state v2 wired as an opt-in mode`
- Body: the full contents of `reports/context-rot-r2/PR-BODY.md`
  (plain Markdown; every table renders on GitHub).

## 5. Post-open checklist

- [ ] The PR diff contains no file outside `crates/pi-coding-agent`,
      `.changes/`, `reports/context-rot-r2/`, `research/context_rot_r2/`,
      `.gitattributes`, and the W58 docs surface (`README.md`,
      `resources/agent/docs/settings.md`).
- [ ] The five LFS paths render as content, not pointer stubs (step 3).
- [ ] Cross-check the PR body's defect table against `BUG-LEDGER.md` (the
      ledger is the source of truth for statuses; the body was assembled at
      `90fcd17db` - adjust if commits landed after).
- [ ] Never force-push the branch after the PR opens; follow-ups go through
      new commits.

## Notes

- The PR title is the H1 of `reports/context-rot-r2/PR-BODY.md`.
- Reviewers need `git lfs pull` after checkout (the body states this in
  "What is NOT claimed").
- The `.changes/context-rot-r2.md` fragment is for the release notes merge,
  not the PR body.
