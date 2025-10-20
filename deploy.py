#!/usr/bin/env python3
"""Project end-to-end setup helper.

Features:
    * Template-driven env prompt: parses example.env (uncommented=required, commented=optional).
    * Existing .env values (if present) become defaults during prompting.
    * Simple .env writer (key=value only); use version control + example.env for commentary.
    * Docker compose build + deploy with optional dev override (`--dev`).
    * System dependency checks (Docker) with guided install (Linux apt/dnf/yum).
    * Non-env CI mode for build/deploy (omit --env).

Usage examples:
    env full setup:
        python setup.py --env --build --deploy

    Build only (non-env, using existing .env):
        python setup.py --build

    Deploy services:
        python setup.py --deploy

Safety:
    Will not overwrite an existing .env unless --force (or user confirms envly).
"""

from __future__ import annotations

import argparse
import platform
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ENV_PATH = REPO_ROOT / '.env'
EXAMPLE_ENV_PATH = REPO_ROOT / 'example.env'
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
DB_ID_RE = re.compile(r'^[a-f0-9]{32}$', re.IGNORECASE)
TEMPLATE_VAR_RE = re.compile(r'^(?P<commented>#\s*)?(?P<key>[A-Z0-9_]+)=(?P<value>.*)$')
FORCE = False


def user_confirm(prompt: str, cancel_msg: str = '[INFO] Aborted.') -> None:
    response = ''
    while response.lower() != 'y':
        if FORCE or not sys.stdin.isatty():  # auto-confirm in non-interactive / force mode
            print(f"{prompt} (auto-confirmed)")
            return
        response = input(f"{prompt} (y/n): ").strip().lower()
        if response == 'n':
            print(cancel_msg)
            sys.exit(2)


def parse_existing_env(path: Path) -> dict[str, str]:
    """Load existing .env key=value pairs (ignoring comments)."""
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        data[k.strip()] = v.strip()
    return data


def check_command(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)
        return True
    except Exception:
        return False


def ensure_docker() -> None:
    """Ensure docker is available; if missing and env, prompt to install on Linux.

    Falls back to manual instructions if user declines or unsupported platform.
    """
    if check_command(['docker', '--version']):
        return
    user_confirm('[WARN] Docker not found. Install now?',
                 'Please install Docker manually: https://docs.docker.com/engine/install/ then re-run.')
    system = platform.system().lower()
    if system != 'linux':
        print('[ERROR] Automated install only supported for Linux here. Use official docs for your platform.')
        sys.exit(2)
    install_cmds: list[list[str]] = []
    if check_command(['which', 'apt-get']):
        install_cmds = [
            ['sudo', 'apt-get', 'update'],
            ['sudo', 'apt-get', 'install', '-y', 'docker.io'],
            ['sudo', 'systemctl', 'enable', '--now', 'docker'],
        ]
    elif check_command(['which', 'dnf']):
        install_cmds = [
            ['sudo', 'dnf', 'install', '-y', 'docker'],
            ['sudo', 'systemctl', 'enable', '--now', 'docker'],
        ]
    elif check_command(['which', 'yum']):
        install_cmds = [
            ['sudo', 'yum', 'install', '-y', 'docker'],
            ['sudo', 'systemctl', 'enable', '--now', 'docker'],
        ]
    else:
        print('[ERROR] Unsupported package manager; install Docker manually. Aborting.')
        sys.exit(2)
    for cmd in install_cmds:
        print(f'[INFO] Running: {" ".join(cmd)}')
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f'[ERROR] Command failed: {cmd} (exit={e.returncode})')
            sys.exit(2)
    if check_command(['docker', '--version']):
        print('[OK] Docker installed successfully.')
    else:
        print('[ERROR] Docker installation failed; verify manually.')
        sys.exit(2)


def env_prompt(skip_if_exists: bool) -> None:
    """Prompt user for environment variable values based on template entries.

    Required entries MUST be provided (uncommented in template). Optional entries
    default to keeping commented unless a value entered.
    """
    print('\n == Environment Configuration ==')
    existing: dict[str, str] = {}
    env_exists = ENV_PATH.exists()
    env_text = ''
    if env_exists:
        env_text = ENV_PATH.read_text(encoding='utf-8')
        for line in env_text.splitlines():
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            existing[k.strip()] = v.strip()
    existing = parse_existing_env(ENV_PATH)
    tty = sys.stdin.isatty()
    res = []
    comments = []
    for line in EXAMPLE_ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('#') and '=' not in line:
            if comment := line.lstrip('#').strip():
                comments.append(comment)
            res.append(line)
            continue
        try:
            match = TEMPLATE_VAR_RE.match(line)
            if not match:
                continue
            key = match.group('key')
            example = match.group('value').strip() or ''
            commented = match.group('commented') or ''
            is_required = not bool(commented)
            is_commented = bool(commented) and not existing.get(key)
            if is_commented:
                line = f'{commented}{key}={example}'
                continue
            default = existing.get(key) or example
            if not tty:
                line = f'{commented}{key}={default}'
                continue
            is_email = (not key.startswith('NOTION_PROP_') and key.endswith('_EMAIL')) or key in {'GMAIL_USER'}
            is_db_id = key.endswith('_DB_ID')
            if skip_if_exists and (not is_required or existing.get(key)):
                commented = '# ' if example == default else ''
                line = f'{commented}{key}={default}'
                continue

            for comment in comments:
                print(comment)
            prompt = f'   {key} [{default}]: '
            while True:
                val = input(prompt).strip()
                if not val:
                    if is_required and not default:
                        print('  Value required.')
                        continue
                    val = default
                if val == '#':
                    break
                if val and is_email and not EMAIL_RE.match(val):
                    print('  Invalid email format.')
                    continue
                if val and is_db_id and not DB_ID_RE.match(''.join(val.split('-'))):
                    print('  DB IDs should be 32 hex chars. (32 hex chars from Notion URL)')
                    continue
                break
            line = f'{key}={val}'
            if val == '#' or val == example:
                line = f'# {key}={default}'
        finally:
            comments = []
            res.append(line)
    final_env = '\n'.join(res) + '\n'
    if env_exists and env_text == final_env:
        print('[INFO] .env unchanged')
        return
    print('\n== Generated .env Preview ==')
    print(final_env)

    user_confirm('Continue overwriting .env? ', '[INFO] Aborted .env overwrite')
    ENV_PATH.write_text(final_env, encoding='utf-8')
    print(f'[OK] Wrote .env to {ENV_PATH.resolve()}')


def docker_build(dev: bool) -> None:
    ensure_docker()
    files = ['docker-compose.yml']
    if dev:
        files.append('docker-compose.dev.yml')
    file_args: list[str] = []
    for f in files:
        file_args.extend(['-f', f])
    print('[INFO] Building services via docker compose ...')
    cmd = ['docker', 'compose', *file_args, 'build']
    subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)
    print('[OK] docker compose build complete.')


def docker_run(dev: bool) -> None:
    files = ['docker-compose.yml']
    if dev:
        files.append('docker-compose.dev.yml')
    file_args: list[str] = []
    for f in files:
        file_args.extend(['-f', f])
    cmd = ['docker', 'compose', *file_args, 'up', '-d']
    print('[INFO] Starting services via docker compose ...')
    subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)
    print('[OK] Services running.')
    if dev:
        print('[INFO] Running in dev mode; connect debugpy debugger on port 12345.')


def notion_deploy() -> None:
    if not check_command([
        'docker',
        'run',
        '--rm',
        '--env-file', '.env',
        '-v', './.env:/app/.env',
        '-it', 'notion-automation',
        '--check-notion-schema',
    ]):
        print('[ERROR] Notion deployment failed; see above for details.')
        sys.exit(2)
    print('[OK] Notion deployment complete.')


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='End-to-end project setup helper.')
    p.add_argument('--env', action='store_true', help='Prompt for required environment variables.')
    p.add_argument('--force', action='store_true', help='Answer "yes" to all prompts.')
    p.add_argument('--build', action='store_true', help='docker compose build (supports dev override).')
    p.add_argument('--run', action='store_true', help='docker compose up (supports dev override).')
    p.add_argument('--all', action='store_true',
                   help='Do env env (if absent), notion validation, build, and run in one step.')
    p.add_argument('--dev', action='store_true', help='Use docker-compose.dev.yml override and ensure ENV=dev.')
    p.add_argument('--notion', action='store_true',
                   help='Audit & ensure required Notion database properties (not part of --all).')
    return p.parse_args()


def main() -> None:
    global FORCE
    args = parse_args()
    FORCE = args.force

    if args.env or args.all:
        env_prompt(skip_if_exists=args.all)

    if args.build or args.all:
        docker_build(dev=args.dev)

    if args.notion or args.all:
        notion_deploy()

    if args.run or args.all:
        docker_run(dev=args.dev)


if __name__ == '__main__':  # pragma: no cover
    try:
        main()
    except KeyboardInterrupt:
        print('\n[INTERRUPT] Setup aborted.')
        sys.exit(130)
