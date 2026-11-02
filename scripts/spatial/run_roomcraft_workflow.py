"""Dispatch a scene workflow through the required Codex CLI + Kimi provider.

This entry point never imports bpy or launches Blender itself.
Load the user's private model environment before running it.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, default=root / 'runs/roomcraft-standard')
    parser.add_argument('--stage', choices=['inspect', 'prepare', 'lighting-previews', 'render', 'export', 'verify', 'all'], default='verify')
    parser.add_argument('--dry-run', action='store_true', help='Write the reviewable task only; do not call a model.')
    args = parser.parse_args()
    directory = args.run_dir.resolve()
    try:
        directory.relative_to(root / 'runs')
    except ValueError:
        parser.error('Run directory must be inside this project runs/.')
    workflow = directory / 'workflow.py'
    if not workflow.is_file():
        parser.error('The Kimi-authored workflow.py must exist before dispatch.')
    if not (directory / 'config.json').is_file():
        parser.error('config.json is required.')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    jobs = directory / 'jobs'
    jobs.mkdir(exist_ok=True)
    task = jobs / f'{stamp}-{args.stage}.txt'
    evidence = jobs / f'{stamp}-{args.stage}.json'
    log = jobs / f'{stamp}-{args.stage}.jsonl'
    instruction = f'''You are ALREADY Codex CLI using Kimi K3.1 via ark-kimi-experiment.
Read AGENTS.md. Do not spawn/nest/resume another CLI or agent, and do not change providers.
Run only the requested RoomCraft stage: {args.stage}.
Run folder: {directory.relative_to(root)}
Read its config.json and the CLI of its existing workflow.py, then execute the supported stage yourself through Blender as necessary.
Every Blender operation must be authored and executed by YOU in this Kimi CLI.
Preserve all source and historical files. Use existing fingerprint/resume logic; stale outputs must not be treated as current.
Do not call image generation, change website files, or publish. No secrets/private endpoint IDs in outputs.
For verify, verify only and report actual failures; never hide failures or hard-code passes.
For all/prepare/render/export, finish the requested stage, fix bounded implementation errors, and record actual outputs.
Write a concise final result with files, checks and remaining limitations. Human visual review is separate from automated checks.
'''
    task.write_text(instruction)
    record = {'stage': args.stage, 'requestedAtUTC': stamp, 'workflow': 'Codex CLI',
              'modelLabel': 'Kimi K3.1', 'providerLabel': 'ark-kimi-experiment',
              'scriptSha256Before': hashlib.sha256(workflow.read_bytes()).hexdigest(),
              'configSha256': hashlib.sha256((directory/'config.json').read_bytes()).hexdigest(),
              'task': str(task.relative_to(root)), 'log': str(log.relative_to(root)),
              'status': 'prepared'}
    evidence.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    if args.dry_run:
        print(json.dumps({'status':'prepared_not_executed','task':str(task),'evidence':str(evidence)}))
        return 0
    lock = directory / '.dispatch.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        parser.error('Another dispatch lock exists. Check that run before retrying.')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(str(os.getpid()))
        record['status'] = 'running'
        evidence.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        code = subprocess.call([sys.executable, str(root/'scripts/spatial/run_codex_kimi.py'),
                                str(task), '--output', str(log), '--sandbox', 'danger-full-access',
                                '--reasoning', 'medium'], cwd=root)
        record.update(status='cli_finished' if code == 0 else 'cli_failed', exitCode=code,
                      scriptSha256After=hashlib.sha256(workflow.read_bytes()).hexdigest())
        evidence.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        return code
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
