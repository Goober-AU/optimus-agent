# PUSH REHEARSAL - context rot iteration 2 (W90, dry-run only)

Rehearsal of every step in PUSH-CHECKLIST.md, executed 2026-10-20 from the
W90 worktree `C:/Users/openclawuser/optimus-rot2-w90-rehearsal` (branch
`rot2/w90-rehearsal`). No push was executed: every remote-touching command
below was either a read (`ls-remote`, LFS batch probe) or a `--dry-run`.
The rehearsal worktree shares the repository (remotes, refs, hooks
directory, LFS object store) with the integration worktree
`C:/Users/openclawuser/optimus-rot2-20261020`, so the dry-run results
transfer to the integrator's run unchanged.

The integration branch is LIVE: it moved from `f6abca389` (184 commits
over base) to `9a867ad5f` (187, three docs commits: W83 PR-body
read-through, taskState docs sync, worktree debris verdicts) while the
rehearsal ran. Every check below was re-verified at `9a867ad5f` unless
noted. The integrator re-runs the one-liners in section 7 at push time.

## 1. Preconditions (checklist section 0)

- 0.1 Status/descends-from: VERIFIED at `9a867ad5f`. The integration
  worktree is clean and on `improve/context-rot-iteration-2`; the tip
  descends from the assembly tip `90fcd17db`, the body anchors
  `08f34308b` and `ac5edde2b`, and the base `6000d6bc1`
  (`git merge-base --is-ancestor` x4, all pass). Deviation: the
  checklist's literal "listing only docs commits" for
  `90fcd17db..HEAD` is stale - the range now also carries the landed
  fix/test/refactor/chore commits for D33 (`cea3a4496`, `6d85cc48f`),
  D34 (`6894f6a6d`), the S5/S6/S7 hygiene fixes, the W87 cleanups and
  the M2 clippy pass. All are documented in the PR body's defect table;
  the checklist preamble ("the W61 PR assembly and any later
  report/gate/matrix commits are expected on top") is the operative
  reading. No action needed beyond awareness.
- 0.2 BUG-LEDGER: NOT re-verified - the file is outside W90's file map
  (brief allows reading only PUSH-CHECKLIST.md, PR-BODY.md and git
  config/remotes). Remains an integrator check.
- 0.3 LFS state: VERIFIED. `git lfs ls-files --size` lists exactly the
  five raw-call logs with real sizes, matching the checklist:
  `live/raw-calls.jsonl` 8.1 MB, `live/e3/raw-calls.jsonl` 2.4 MB,
  `live/e3adv/raw-calls.jsonl` 1.4 MB,
  `live/e5/live/e3-typed-raw-calls.jsonl` 2.5 MB,
  `compactbench_adapter/live/raw-calls.jsonl` 262 KB. The five pointer
  blobs at the tip carry OIDs `f8fb4b876b...`/`aa32569017...`/
  `28d0b2cd94...`/`5f94685a0e...`/`62a32cc09f...` and the local LFS
  store holds all five objects with byte-exact sizes (8131489, 2424618,
  1402004, 2542234, 262069).
- 0.4 Remote: VERIFIED. `git remote -v` shows
  `fork https://github.com/Goober-AU/optimus-agent.git` (fetch+push,
  no pushurl override) and `origin https://github.com/telemusai/optimus-agent.git`.

## 2. Step 1 dry-run: `git push fork improve/context-rot-iteration-2`

Executed as `git -c core.hooksPath=<empty> -c credential.username=Goober-AU
push --dry-run fork improve/context-rot-iteration-2` (rationale below),
twice (once at `f6abca389`, re-run after the branch moved):

```
To https://github.com/Goober-AU/optimus-agent.git
 * [new branch]          improve/context-rot-iteration-2 -> improve/context-rot-iteration-2
```

rc 0 both times. Findings:

- The remote accepts the refspec; the branch is ABSENT on the fork
  (verified via read-only `git ls-remote --heads fork`), so the real
  push CREATES the ref - fast-forward by construction, force never
  needed. `--dry-run` transferred nothing.
- Hook caveat (not in the checklist): on this host's git 2.53.0,
  `git push --dry-run` STILL RUNS the pre-push hook (verified
  empirically with a marker hook in a throwaway repo). The shared
  pre-push hook is `git lfs pre-push`, which uploads LFS objects on a
  real run. A naive dry-run could therefore attempt a real LFS upload.
  W90 neutralized the hook per-invocation with a one-shot
  `-c core.hooksPath=<empty dir>` override - no mutation of the shared
  hooks directory, no persistent config change.
- Auth: the fork push needs GitHub credentials (no anonymous push).
  A non-interactive GCM lookup for github.com WITHOUT a username hint
  fails on this host (stored credentials exist for at least two
  accounts; GCM wants an account-selection prompt). With
  `credential.username=Goober-AU` the stored
  `git:https://Goober-AU@github.com` credential is returned
  non-interactively (verified via `git credential fill`). For the real
  run the integrator either completes the one-time GCM prompt or uses
  the pinned variant in section 7.

## 3. Step 2 dry-run: `git lfs push --dry-run fork --all`

Branch-scoped first (the precise equivalent for this push):
`git lfs push --dry-run fork improve/context-rot-iteration-2` - rc 0,
enumerates exactly the five objects, OIDs matching the tip's pointers:

```
push 62a32cc09fbaf6c114704641c4adf584a7769b648012d9887e1a2d648289532e => research/context_rot_r2/compactbench_adapter/live/raw-calls.jsonl
push aa325690179a58bece30f1ca3aea4b66e15852fabc0f37c048f00078b0bdbf67 => research/context_rot_r2/live/e3/raw-calls.jsonl
push 28d0b2cd94243d3c5120988fd9f380a567583174787225e2b9784136035e33ca => research/context_rot_r2/live/e3adv/raw-calls.jsonl
push 5f94685a0ef2566e9c1bdab09c9f43f9eb857dd0105cd7a17d7fc3c2db1ac119 => research/context_rot_r2/live/e5/live/e3-typed-raw-calls.jsonl
push f8fb4b876b4db64f3503b587dfd3fc2a982e42161889355c69c788f45df661d0 => research/context_rot_r2/live/raw-calls.jsonl
```

The checklist's exact `git lfs push --dry-run fork --all` was also
launched: it enumerated the same five objects PLUS
`e3b0c442...` (the empty-file object) =>
`bench/analysis/__init__.py`, which is reachable from a fork-tracking
branch (`fork/bench/private-helper-budget-experiment`) and is already
present on the fork. The `--all` walk spans every local ref including
remote-tracking refs (fork + origin, hundreds of branches) and had not
finished after 15+ minutes; the enumeration was stable at those six
objects. Verdict: `--all` works but is slow and is a superset; the
branch-scoped command is the precise verification for this push. No
harm either way - the LFS server dedupes by OID.

LFS endpoint health (read-only batch probe, operation=download, one
OID, authenticated): HTTP 200 with per-object
`{"code":404,"message":"Object does not exist on the server"}` - the
fork's LFS API is ENABLED and the objects are absent, the correct
pre-push state. No "LFS not enabled" failure mode for the real run.

## 4. Step 3: PR-open command line

- Title: the H1 of `reports/context-rot-r2/PR-BODY.md` is
  `Context rot iteration 2: compaction defect fixes, production-path
  benchmarks, typed-state v2 wired as an opt-in mode` - identical to
  the checklist's `--title` argument. Verified unchanged at both
  `f6abca389` and `9a867ad5f` (W83 revised the body's prose and its
  finalization anchor to `ac5edde2b`/177, not the H1).
- Body file: `reports/context-rot-r2/PR-BODY.md` exists at the tip
  (700 lines / ~61 KB at the W90 snapshot; W83's read-through pass is
  the latest revision). `--body-file` is a RELATIVE path - run the
  `gh` command from the integration worktree root, or use an absolute
  path.
- `gh` CLI: NOT installed on the campaign host (re-verified:
  `'gh' is not recognized`). The PR-open step runs on a machine with
  `gh` authenticated to the fork account, or via the web fallback.
- `--base main`: telemusai/optimus-agent's main is currently exactly
  `6000d6bc1` (verified via live `git ls-remote origin refs/heads/main`),
  so the GitHub PR diff equals the campaign's base-relative diff.
- `--head Goober-AU:improve/context-rot-iteration-2` matches the
  pushed branch name exactly.
- Web fallback URL is a valid GitHub compare form:
  `https://github.com/telemusai/optimus-agent/compare/main...Goober-AU:optimus-agent:improve/context-rot-iteration-2`.
  Web fallback steps: open the URL, confirm the diff/base/head, paste
  the title and the full PR-BODY.md contents, open the PR.

## 5. Step 4: hooks that fire on the real push

- `git config core.hooksPath` is UNSET; there is no `.githooks/`
  directory in the worktree. The effective hooks directory is the
  shared common-dir hooks
  (`.../source/.git/hooks`, common to every worktree of this repo,
  including the integrator's).
- That directory contains ONLY the four git-lfs hooks installed by
  `git lfs install` (plus samples): `pre-push`, `post-checkout`,
  `post-commit`, `post-merge`. Each is the standard LFS shim: verify
  git-lfs is on PATH, then exec `git lfs <hook> "$@"`.
- What the LFS pre-push hook does on the REAL
  `git push fork improve/context-rot-iteration-2`: it receives the
  pushed refspecs on stdin, scans the pushed commit range for LFS
  pointers, contacts the fork's LFS API and uploads every missing
  object (the five raw-call logs, ~14.7 MB total) BEFORE git transfers
  the pack and creates the ref. If the LFS upload fails (auth, quota,
  network), the hook exits non-zero and git ABORTS the push - the
  branch is not created with dangling pointers. Consequence for the
  checklist: step 2's LFS upload rides along with step 1
  automatically; the explicit `git lfs push` afterwards is
  confirmatory and will be a no-op for these five objects (the push
  updates the fork-tracking ref, and `git lfs push` filters objects the
  remote already references).
- `.husky/pre-commit` (exec `bash scripts/check.sh`) exists in the
  worktree but husky is NOT active: `core.hooksPath` is unset and the
  shared hooks directory holds no husky hooks. It fires on nothing -
  not on commit, and there is no husky pre-push. No workspace-wide
  check runs as part of the push.
- No other pre-push hook anywhere in the chain (no repo-local, no
  global, no system hooks beyond the LFS set).

## 6. Step 5 post-open items (verified by inspection)

- Diff surface over `6000d6bc1..9a867ad5f`: 366 files, ALL within
  `crates/pi-coding-agent/`, `.changes/`, `reports/context-rot-r2/`,
  `research/context_rot_r2/`, `.gitattributes`, plus exactly
  `README.md` and `resources/agent/docs/settings.md`. Nothing outside
  the allowed surface (same result at `f6abca389`, 365 files).
- LFS paths will resolve: the five objects upload with the push (hook,
  section 5) and the local store has byte-exact copies (section 1.3).
  Post-push web check per the checklist: the
  `live/raw-calls.jsonl` blob URL must render as JSONL, not a ~130-byte
  pointer stub.
- PR body vs BUG-LEDGER cross-check: outside W90's file map; the
  integrator runs it (the ledger is the source of truth; the body's
  anchor will lag the final tip by however many commits land after
  `9a867ad5f` - the body's own header says the integrator adjusts).

## 7. Exact final command sequence for the integrator

Run from `C:/Users/openclawuser/optimus-rot2-20261020`. The
`-c credential.username=Goober-AU` pin is verified non-interactive on
this host; without it, expect a one-time GCM account prompt.

```
# 0. re-verify (fresh, one-liners)
git status
git log --oneline 90fcd17db..HEAD
git lfs ls-files --size
git remote -v

# 1. push the branch (creates the ref on the fork; the LFS pre-push
#    hook uploads the five objects first, aborting the push on failure)
git -c credential.username=Goober-AU push fork improve/context-rot-iteration-2

# 2. confirm the LFS objects are on the fork (branch-scoped; --all
#    also works but walks every local ref, 15+ min)
git -c credential.username=Goober-AU lfs push fork improve/context-rot-iteration-2

# 3. verify the pointers on the web (renders as JSONL, not a stub)
# https://github.com/Goober-AU/optimus-agent/blob/improve/context-rot-iteration-2/research/context_rot_r2/live/raw-calls.jsonl

# 4. open the PR (gh machine, from the integration worktree root)
gh pr create --repo telemusai/optimus-agent \
  --base main \
  --head Goober-AU:improve/context-rot-iteration-2 \
  --title "Context rot iteration 2: compaction defect fixes, production-path benchmarks, typed-state v2 wired as an opt-in mode" \
  --body-file reports/context-rot-r2/PR-BODY.md
# web fallback:
# https://github.com/telemusai/optimus-agent/compare/main...Goober-AU:optimus-agent:improve/context-rot-iteration-2
```

Never force-push; follow-ups go through new commits (checklist rule).

## 8. Deviations and open notes

1. Precondition 0.1's "only docs commits" wording is stale relative to
   the tip (fix/test/refactor/chore commits for D33/D34/S/M2 waves are
   on top of the assembly tip); the state matches the checklist's own
   preamble and the PR body. No action.
2. `git push --dry-run` fires the pre-push hook on git 2.53.0 - anyone
   re-running a dry-run should neutralize the hook (one-shot
   `-c core.hooksPath=<empty>`) or accept the LFS-upload side effect.
3. `git lfs push --dry-run fork --all` is slow (15+ min, all local
   refs) and enumerates one extra, already-on-fork empty object
   (`bench/analysis/__init__.py`) besides the five; the branch-scoped
   variant is the precise check.
4. GCM account ambiguity: pin the username (verified) or complete the
   one-time prompt.
5. W90 did not re-verify BUG-LEDGER.md (file map) - the integrator's
   preconditions 0.2 and the body/ledger cross-check remain theirs.
6. The integration branch moved during the rehearsal
   (`f6abca389` -> `9a867ad5f`); all dry-runs re-verified at the new
   tip. Re-run section 7 step 0 at push time.
