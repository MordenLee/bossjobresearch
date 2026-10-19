"""Durable single-writer task journal and explicit pause/resume gates. No browser IO."""
import argparse
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from workflow import read, write, read_ledger, append_ledger, now

REASONS = {'LOGIN_REQUIRED', 'LOGIN_UNKNOWN', 'CHALLENGE', 'ACCESS_DENIED',
           'QUOTA_LOW', 'QUOTA_CHECK', 'USER_PAUSE', 'NEEDS_INPUT',
           'UPGRADE_CONFIRMATION', 'RESULT_UNCERTAIN', 'READ_ERROR'}
USER_REASONS = {'LOGIN_REQUIRED', 'LOGIN_UNKNOWN', 'CHALLENGE', 'USER_PAUSE',
                'NEEDS_INPUT', 'RESULT_UNCERTAIN', 'ACCESS_DENIED', 'READ_ERROR'}
PHASES = {'planning', 'login', 'search', 'detail', 'review', 'export', 'execute'}
TASK_STATES = {'READY', 'RUNNING', 'PAUSED', 'BUDGET_COMPLETED', 'COMPLETED', 'FAILED'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def candidate_digest(candidate):
    # Execution authorization is not a qualification fact and is checked independently.
    return digest({k:v for k,v in candidate.items() if k != 'authorization'})


@contextmanager
def writer_lock(run_dir):
    """Do not guess whether an abandoned writer is dead. Operator verifies before removal."""
    root = Path(run_dir); root.mkdir(parents=True, exist_ok=True)
    path = root / '.writer.lock'
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError('运行目录已有写入锁；核实原进程已退出后再人工移除锁') from exc
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps({'pid': os.getpid(), 'at': now()}))
        yield
    finally:
        path.unlink()


class Runtime:
    def __init__(self, run_dir):
        self.root = Path(run_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = read(self.root / 'runtime-config.json') if (self.root / 'runtime-config.json').exists() else {}
        self.task_id = self.config.get('task_id') or digest(str(self.root.resolve()))[:24]

    def load(self):
        events = read_ledger(self.root / 'task-events.jsonl')
        if events:
            for n, event in enumerate(events, 1):
                if event.get('sequence') != n or event.get('task_id') != self.task_id:
                    raise ValueError('任务事件序号或 task_id 不一致；停止并保留证据')
            return events[-1]
        return dict(schema_version=3, task_id=self.task_id, sequence=0, task_state='READY',
                    phase='planning', pause_reason=None, checkpoint={}, escalation=None)

    def commit(self, **changes):
        state = self.load()
        state.update(changes)
        if state.get('task_state') not in TASK_STATES or state.get('phase') not in PHASES:
            raise ValueError('无效任务状态或阶段')
        if state['task_state'] == 'PAUSED' and state.get('pause_reason') not in REASONS:
            raise ValueError('暂停必须有明确理由')
        state.update(sequence=state['sequence'] + 1, event_id=uuid.uuid4().hex, at=now())
        # Event is the recovery authority. A crash before snapshot replace is recoverable.
        append_ledger(self.root / 'task-events.jsonl', state)
        write(self.root / 'task-state.json', state)
        return state

    def pause(self, reason, phase=None, checkpoint=None, **details):
        if reason not in REASONS or (phase is not None and phase not in PHASES):
            raise ValueError('无效暂停理由或阶段')
        old = self.load()
        return self.commit(task_state='PAUSED', phase=phase or old['phase'], pause_reason=reason,
                           token=uuid.uuid4().hex, acknowledged=False,
                           checkpoint=checkpoint if checkpoint is not None else old['checkpoint'],
                           pause_details=details)

    def acknowledge(self, token, evidence):
        state = self.load()
        if state['task_state'] != 'PAUSED' or state.get('token') != token or not evidence.strip():
            raise ValueError('暂停状态、token 或恢复依据无效')
        if state['pause_reason'] not in USER_REASONS:
            raise ValueError('此暂停需专门的额度刷新或升级决定，普通继续不能解除')
        return self.commit(acknowledged=True, acknowledgement=evidence)

    def ensure_ready(self):
        state = self.load()
        if state['task_state'] == 'COMPLETED':
            raise ValueError('任务已完成；追加工作须建立新批次并保留历史')
        if state['task_state'] == 'PAUSED' and not state.get('acknowledged'):
            raise ValueError('任务仍暂停：' + str(state['pause_reason']))
        return state

    def start(self, phase):
        self.ensure_ready()
        if phase not in PHASES:
            raise ValueError('无效阶段')
        return self.commit(task_state='RUNNING', phase=phase, pause_reason=None, token=None,
                           acknowledged=False)

    def request_upgrade(self, proposal, cost_notice):
        if not proposal.strip() or not cost_notice.strip():
            raise ValueError('升级需具体方案与 Token 成本说明')
        request = dict(request_id=uuid.uuid4().hex, task_id=self.task_id, proposal=proposal,
                       proposal_hash=digest(proposal), cost_notice=cost_notice, decision='pending')
        self.commit(escalation=request)
        return self.pause('UPGRADE_CONFIRMATION', proposal=request)

    def decide_upgrade(self, token, approved, evidence):
        state = self.load(); request = state.get('escalation')
        if (state['task_state'] != 'PAUSED' or state['pause_reason'] != 'UPGRADE_CONFIRMATION'
                or token != state.get('token') or not request or not evidence.strip()):
            raise ValueError('升级决定没有匹配的当前请求、token 或用户依据')
        request = dict(request, decision='approved' if approved else 'declined', evidence=evidence, decided_at=now())
        return self.commit(escalation=request, task_state='READY', pause_reason=None, token=None,
                           acknowledged=False)

    def check_upgrade(self, proposal):
        request = self.load().get('escalation') or {}
        if (request.get('decision') != 'approved' or request.get('task_id') != self.task_id
                or request.get('proposal_hash') != digest(proposal)):
            raise ValueError('当前任务和方案没有升级许可')

    def quota_gate(self, processed):
        policy = self.config.get('quota')
        if not policy:
            return
        snapshot_path = self.root / 'quota-state.json'
        q = read(snapshot_path) if snapshot_path.exists() else {}
        checked = q.get('checked_at_epoch', 0)
        if (q.get('source') != 'mcp__codex_app__get_usage_limits' or not isinstance(checked, (int, float))
                or not 0 <= time.time() - checked <= policy.get('max_age_seconds', 900)
                or type(q.get('remaining_percent')) not in (int, float)
                or not 0 <= q['remaining_percent'] <= 100
                or type(q.get('checked_processed_total')) is not int
                or q['checked_processed_total'] > processed
                or processed - q['checked_processed_total'] >= policy.get('check_after_records', 100)):
            self.pause('QUOTA_CHECK', processed=processed)
            raise ValueError('需主 Agent 查询并保存当前真实额度快照')
        if q['remaining_percent'] < policy.get('minimum_remaining_percent', 5):
            self.pause('QUOTA_LOW', processed=processed)
            raise ValueError('实际剩余额度低于当前任务阈值')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('inspect')
    p = sub.add_parser('ack'); p.add_argument('--token', required=True); p.add_argument('--evidence', required=True)
    p = sub.add_parser('pause'); p.add_argument('--reason', choices=sorted(REASONS), required=True)
    p = sub.add_parser('request-upgrade'); p.add_argument('--proposal', required=True); p.add_argument('--cost-notice', required=True)
    p = sub.add_parser('decide-upgrade'); p.add_argument('--token', required=True); p.add_argument('--decision', choices=['approve', 'decline'], required=True); p.add_argument('--evidence', required=True)
    p = sub.add_parser('refresh-quota'); p.add_argument('--snapshot', required=True); p.add_argument('--processed', type=int, required=True)
    args = parser.parse_args(); runtime = Runtime(args.run_dir)
    if args.command == 'inspect':
        print(json.dumps(runtime.load(), ensure_ascii=False)); return
    with writer_lock(args.run_dir):
        if args.command == 'ack':
            result = runtime.acknowledge(args.token, args.evidence)
            # Legacy Guard marker is an input receipt, never proof of authenticated session.
            if (runtime.root / 'status.json').exists():
                legacy = read(runtime.root / 'status.json')
                if legacy.get('state') == 'WAITING_FOR_USER' and legacy.get('token') == args.token:
                    write(runtime.root / 'continue.json', {'token': args.token})
        elif args.command == 'pause':
            result = runtime.pause(args.reason)
        elif args.command == 'request-upgrade':
            result = runtime.request_upgrade(args.proposal, args.cost_notice)
        elif args.command == 'decide-upgrade':
            result = runtime.decide_upgrade(args.token, args.decision == 'approve', args.evidence)
        else:
            state = runtime.load()
            if state.get('pause_reason') not in ('QUOTA_LOW', 'QUOTA_CHECK'):
                raise ValueError('当前不是额度暂停')
            write(runtime.root / 'quota-state.json', read(args.snapshot))
            runtime.quota_gate(args.processed)
            result = runtime.commit(task_state='READY', pause_reason=None, token=None, acknowledged=False)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
