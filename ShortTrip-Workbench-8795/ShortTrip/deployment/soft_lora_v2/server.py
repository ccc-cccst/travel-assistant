"""Manage the pinned, loopback-only local llama.cpp server (Windows)."""
import argparse
import ctypes
from ctypes import wintypes
import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'run' / 'server.json'
KERNEL = ctypes.WinDLL('kernel32', use_last_error=True)
KERNEL.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
KERNEL.OpenProcess.restype = wintypes.HANDLE
KERNEL.CloseHandle.argtypes = [wintypes.HANDLE]
KERNEL.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
KERNEL.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
KERNEL.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]

def identity(handle):
    buffer = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buffer))
    times = [wintypes.FILETIME() for _ in range(4)]
    if not KERNEL.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not KERNEL.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    return dict(executable=str(Path(buffer.value).resolve()), created=times[0].dwHighDateTime * 2**32 + times[0].dwLowDateTime)

def owned_handle(state, terminate=False):
    handle = KERNEL.OpenProcess(0x1000 | (1 if terminate else 0), False, state['pid'])
    if not handle:
        return None
    if identity(handle) != state['identity']:
        KERNEL.CloseHandle(handle)
        return None
    return handle

def request(config, path):
    # Explicitly bypass system HTTP proxies for loopback requests.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:{config["port"]}{path}', timeout=3) as response:
        return json.load(response)

def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('action', choices=['start', 'status', 'stop'])
    args = parser.parse_args()
    config = json.loads((ROOT / 'deployment.json').read_text(encoding='utf-8'))
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else None
    handle = owned_handle(state, args.action == 'stop') if state else None
    if args.action == 'stop':
        if handle:
            try:
                if not KERNEL.TerminateProcess(handle, 0):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                KERNEL.CloseHandle(handle)
            STATE.unlink(missing_ok=True)
            print('Stopped local model server.')
        else:
            print('No matching owned server is running.')
        return
    if handle:
        KERNEL.CloseHandle(handle)
        health = request(config, '/health')
        print(json.dumps(dict(running=True, pid=state['pid'], health=health,
                              base_url=f'http://127.0.0.1:{config["port"]}/v1'), ensure_ascii=False))
        return
    if args.action == 'status':
        print(json.dumps(dict(running=False)))
        return
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', config['port']))
    executable = ROOT / config['executable']
    arguments = [str(executable), '--model', str(ROOT / config['model_path']), '--alias', config['alias'],
        '--host', '127.0.0.1', '--port', str(config['port']), '--ctx-size', str(config['context']),
        '--parallel', '1', '--device', 'Vulkan0', '--gpu-layers', 'auto', '--fit', 'on',
        '--batch-size', '128', '--ubatch-size', '128', '--flash-attn', 'on',
        '--jinja', '--reasoning', 'off',
        '--temp', '0.7', '--top-p', '0.8', '--top-k', '20', '--min-p', '0', '--offline']
    if not (ROOT / config['model_path']).is_file():
        raise FileNotFoundError('The trained GGUF model has not been downloaded yet.')
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    STATE.parent.mkdir(exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    stdout = logs / (stamp + '.stdout.log')
    stderr = logs / (stamp + '.stderr.log')
    with stdout.open('wb') as out, stderr.open('wb') as err:
        process = subprocess.Popen(arguments, cwd=executable.parent, stdout=out, stderr=err,
            stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    handle = KERNEL.OpenProcess(0x1000, False, process.pid)
    if not handle:
        raise RuntimeError(f'Cannot inspect new process; inspect {stderr}')
    try:
        state = dict(pid=process.pid, identity=identity(handle), stdout=str(stdout), stderr=str(stderr), arguments=arguments)
    finally:
        KERNEL.CloseHandle(handle)
    STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
    for seconds in range(240):
        if process.poll() is not None:
            raise RuntimeError(f'Server exited with {process.returncode}; inspect {stderr}')
        try:
            if request(config, '/health').get('status') == 'ok':
                print(json.dumps(dict(ready=True, pid=process.pid, base_url=f'http://127.0.0.1:{config["port"]}/v1'), ensure_ascii=False), flush=True)
                return
        except (OSError, ValueError):
            pass
        if seconds % 10 == 0:
            print(f'Loading model... {seconds}s; log: {stderr.name}', flush=True)
        time.sleep(1)
    raise TimeoutError(f'Server still loading; inspect {stderr}; process preserved.')

if __name__ == '__main__':
    main()
