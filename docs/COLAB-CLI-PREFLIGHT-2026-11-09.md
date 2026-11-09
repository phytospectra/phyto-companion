# Colab CLI preflight — 2026-11-09

Scope: local CLI inspection and one non-allocating account-usage query. No VM was
created, code uploaded, package installed, model downloaded, or training started.
No credentials or session-state files were printed.

## Verified installed interface

- Executable: `/Users/subhadipbanerjee/.local/bin/colab`.
- Installation: uv tool environment `google-colab-cli`.
- Version returned by `colab version`: `0.7.6.dev8+g5a119adc8`.
- `colab --version` is **not** supported; use `colab version`.
- Global `--auth` choices are `oauth2` and `adc`. Installed help says the default
  is `oauth2`; the bundled README's statement that it defaults to `adc` is stale.
  Specify the intended strategy explicitly when reproducing an approved job.
- `colab run` supports GPU requests `T4`, `L4`, `G4`, `H100`, `A100`, as well as
  TPU requests `v5e1`, `v6e1`. Availability/entitlement was not tested.
- `colab run --timeout SECONDS SCRIPT [ARGS...]` creates a fresh runtime, executes
  the local Python script, and attempts cleanup. The default execution timeout
  is only 30 seconds. `--keep` preserves the runtime instead.
- `colab new`, `exec`, `install`, `upload`, `download`, `stop`, `usage`, `sessions`
  and `status` exist. Their availability does not establish successful GPU access.

## Account observation

A non-interactive `colab usage` query completed with exit 0 and reported:

- Current balance: **0.00 compute units**.
- Usage rate: **0.00/hour**.
- Active assignments: **0**.

This is an observation of the currently CLI-authenticated account, not a
verification or rejection of the user's stated Pro+ subscription. Before a paid
job, confirm that the CLI is using the intended subscription account and that
usable compute units are available. Do not print account credentials or token
files to diagnose the association. No browser sign-in or account switching was
performed in this preflight.

## Important implementation details

The installed Python source was inspected under the uv tool's
`site-packages/colab_cli/`:

1. `commands/run.py` implements teardown in `finally`, but `_teardown` swallows
   kernel-stop/unassign failures and still prints a termination message. After an
   approved job, verify backend assignments/usage; do not treat that message as
   proof that billing has stopped.
2. `--timeout` applies to code execution, not the complete allocation/setup/job
   lifecycle and not a hard compute-unit budget. Use an independent supervisory
   deadline and post-job assignment verification. A CLI process killed before
   cleanup may leave a runtime allocated.
3. `--env KEY=VALUE` is embedded in the transmitted Python payload. The successful
   execution path logs that payload and its outputs to CLI history. **Do not pass
   API keys, tokens or other secrets via this flag or literal script contents.**
4. The bundled README mentions retrieving output files in its ephemeral-run
   overview, but the inspected `run` implementation and help expose no automatic
   artifact-download option. Preserve required artifacts before teardown through
   an explicitly approved path. Do not assume `/content` survives runtime release.
5. `sessions`/`status` synchronize local session metadata with the server. They do
   not allocate runtimes, but are not purely filesystem-read-only commands.

## Prepared operational sequence (not executed)

1. Confirm intended account, available compute units, approved GPU-hour/compute-unit
   limit and approved data transfer scope. The separate **US$50 OpenRouter pilot
   ceiling does not authorize a Colab compute budget**.
2. Freeze model revision, tokenizer, adapter recipe, dependency versions, data
   hashes and train/development/evaluation split before any downloads or training.
3. Use a uniquely named runtime and record the actual assigned GPU/VRAM. Begin
   with a bounded hardware and one-batch memory probe before committing to a model
   size or full training. Pro+ is not a guarantee of a particular GPU.
4. For artifact-producing training, explicitly manage `new → install/upload →
   exec → download/checksum → stop` with failure-safe cleanup. Alternatively, use
   `run` only when the script has a reviewed artifact-preservation mechanism.
   Do not automatically mount an entire personal Drive.
5. Record start/end usage, execution duration and runtime identity without tokens.
   On interruption or timeout, inspect the named assignment, save permitted
   artifacts if possible, and stop it; verify that the assignment is gone.
6. Keep generated model responses, training logs and weights out of public source
   control unless their publication rights have been established. Never use test
   answers or outputs as training examples.

No GPU readiness, training success, scientific accuracy, subscription purchase,
or installed model has been established by this preflight.
