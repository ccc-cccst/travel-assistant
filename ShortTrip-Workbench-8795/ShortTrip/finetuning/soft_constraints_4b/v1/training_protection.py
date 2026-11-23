"""Crash-safe local training checkpoints and bounded process recovery."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import warnings


def atomic_json(path, value, required=True):
    """A locked progress file must never terminate the training worker."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(20):
            try:
                temporary.replace(path)
                return True
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.1)
    except OSError as exc:
        if required:
            raise
        warnings.warn(f'Progress update skipped: {exc}', RuntimeWarning)
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_identity(path, identity):
    path = Path(path)
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != identity:
            raise ValueError('Resume rejected: model, data or training configuration changed')
    else:
        atomic_json(path, identity)


def training_identity(config):
    model = Path(config['model_dir'])
    excluded = {'resume', 'output_dir', 'adapter_dir', 'work_dir', 'model_dir', 'data_dir'}
    return {
        'format': 1,
        'training': {key: value for key, value in config.items() if key not in excluded},
        'model': {p.name: file_digest(p) for p in sorted(model.iterdir())
                  if p.is_file() and p.suffix in ('.json', '.safetensors', '.model')},
        'data': {name: file_digest(Path(config['data_dir']) / name)
                 for name in ('train.jsonl', 'validation.jsonl', 'test.jsonl', 'manifest.json')},
    }


def seal_checkpoint(checkpoint):
    checkpoint = Path(checkpoint)
    required = ('adapter_config.json', 'adapter_model.safetensors', 'optimizer.pt',
                'scheduler.pt', 'rng_state.pth', 'trainer_state.json')
    for name in required:
        if not (checkpoint / name).is_file() or (checkpoint / name).stat().st_size == 0:
            raise ValueError(f'Incomplete checkpoint: {name}')
    state = json.loads((checkpoint / 'trainer_state.json').read_text(encoding='utf-8'))
    files = {str(p.relative_to(checkpoint)): {'size': p.stat().st_size, 'sha256': file_digest(p)}
             for p in checkpoint.rglob('*') if p.is_file() and p.name != 'complete.json'}
    atomic_json(checkpoint / 'complete.json', {'step': state['global_step'], 'files': files})


def complete_checkpoints(output):
    complete = []
    for path in Path(output).glob('checkpoint-*'):
        try:
            step = int(path.name.removeprefix('checkpoint-'))
            marker = json.loads((path / 'complete.json').read_text(encoding='utf-8'))
            state = json.loads((path / 'trainer_state.json').read_text(encoding='utf-8'))
            required = {'adapter_config.json', 'adapter_model.safetensors', 'optimizer.pt',
                        'scheduler.pt', 'rng_state.pth', 'trainer_state.json'}
            if marker['step'] != step or state['global_step'] != step or not required.issubset(marker['files']):
                continue
            valid = True
            for name, expected in marker['files'].items():
                file = (path / name).resolve()
                if not file.is_relative_to(path.resolve()) or not file.is_file():
                    valid = False
                    break
                if file.stat().st_size != expected['size'] or file_digest(file) != expected['sha256']:
                    valid = False
                    break
            if valid:
                complete.append(path)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(complete, key=lambda p: int(p.name.removeprefix('checkpoint-')))


class ProtectedCheckpointMixin:
    """Keep HF's full optimizer/RNG saving, then publish a verified directory."""
    def _get_output_dir(self, trial):
        stage = getattr(self, '_checkpoint_stage', None)
        return str(stage) if stage is not None else super()._get_output_dir(trial)

    def _rotate_checkpoints(self, use_mtime=False, output_dir=None):
        if not getattr(self, 'protected_checkpoints', False):
            return super()._rotate_checkpoints(use_mtime=use_mtime, output_dir=output_dir)
        if getattr(self, '_checkpoint_stage', None) is not None:
            return
        root = Path(output_dir or self.args.output_dir).resolve()
        checkpoints = complete_checkpoints(root)
        keep = {p.resolve() for p in checkpoints[-2:]}
        if self.state.best_model_checkpoint:
            keep.add(Path(self.state.best_model_checkpoint).resolve())
        for path in checkpoints:
            target = path.resolve()
            if target.parent != root:
                raise ValueError('Checkpoint retention path escaped the run directory')
            if target not in keep:
                try:
                    shutil.rmtree(target)
                except OSError as exc:
                    warnings.warn(f'Old checkpoint cleanup skipped: {exc}')

    def _save_checkpoint(self, model, trial):
        if not getattr(self, 'protected_checkpoints', False):
            return super()._save_checkpoint(model, trial)
        root = Path(super()._get_output_dir(trial)).resolve()
        stage = Path(tempfile.mkdtemp(prefix='.checkpoint-stage-', dir=root))
        name = f'checkpoint-{self.state.global_step}'
        final = root / name
        previous_best = self.state.best_model_checkpoint
        superseded = None
        self._checkpoint_stage = stage
        try:
            super()._save_checkpoint(model, trial)
            checkpoint = stage / name
            if self.state.best_model_checkpoint == str(checkpoint):
                self.state.best_model_checkpoint = str(final)
            self.state.save_to_json(str(checkpoint / 'trainer_state.json'))
            seal_checkpoint(checkpoint)
            if final.exists():
                # Preserve a previous partial directory for diagnosis.
                superseded = root / ('.superseded-' + name + '-' + stage.name.rsplit('-', 1)[-1])
                final.rename(superseded)
            for attempt in range(20):
                try:
                    checkpoint.rename(final)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.1)
            stage.rmdir()
            if superseded is not None and superseded.resolve().parent == root:
                try:
                    shutil.rmtree(superseded)
                except OSError as exc:
                    warnings.warn(f'Superseded checkpoint cleanup skipped: {exc}')
        except Exception:
            self.state.best_model_checkpoint = previous_best
            raise
        finally:
            self._checkpoint_stage = None
        self._rotate_checkpoints(output_dir=str(root))


def make_weighted_sampler(weights, seed):
    import torch

    class EpochWeightedSampler(torch.utils.data.WeightedRandomSampler):
        def __init__(self):
            super().__init__(weights, len(weights), replacement=True)
            self.epoch = 0

        def set_epoch(self, epoch):
            self.epoch = epoch

        def __iter__(self):
            self.generator = torch.Generator().manual_seed(seed + self.epoch)
            return super().__iter__()

    return EpochWeightedSampler()


class RunAlreadyActiveError(RuntimeError):
    pass


@contextmanager
def run_lock(path):
    """The OS releases this lock after a worker crash, including on Windows."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RunAlreadyActiveError('Another training process owns this run') from exc
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def supervise(command, output, max_restarts=3):
    """The worker owns CUDA. This process only waits and restarts crashes."""
    if not isinstance(max_restarts, int) or max_restarts < 0:
        raise ValueError('max_restarts must be a nonnegative integer')
    output = Path(output)
    with run_lock(output / '.supervisor.lock'):
        for attempt in range(max_restarts + 1):
            current = list(command)
            if attempt and '--resume' not in current:
                current.append('--resume')
            atomic_json(output / 'supervisor_status.json',
                        {'state': 'running', 'attempt': attempt + 1, 'max_restarts': max_restarts}, required=False)
            try:
                code = subprocess.call(current)
            except KeyboardInterrupt:
                code = 130
            if code == 0 or code in (77, 78, 130, 3221225786) or code < 0:
                break
            if attempt < max_restarts:
                print(f'Worker exited {code}. Resuming latest complete checkpoint ({attempt + 1}/{max_restarts}).', flush=True)
                time.sleep(3)
        atomic_json(output / 'supervisor_status.json',
                    {'state': 'completed' if code == 0 else 'stopped', 'exit_code': code, 'attempts': attempt + 1}, required=False)
        return code
