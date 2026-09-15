#!/usr/bin/env python3
"""Recompose saved business Evidence once without rerunning tools or mutating history."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.media import EvidenceMediaLoader
from scopex.finalizer.client import StreamingFinalizerClient
from scopex.finalizer.text_report import TextReportComposer


def _saved_json(path: Path, name: str, *, optional: bool = False):
    """Read only named audit artifacts; never extract an archive or follow its paths."""
    limit = 8 * 1024 * 1024
    if path.is_file():
        with zipfile.ZipFile(path) as archive:
            if name not in archive.namelist():
                if optional:
                    return None
                raise ValueError('missing saved artifact: ' + name)
            if archive.getinfo(name).file_size > limit:
                raise ValueError('saved artifact too large')
            raw = archive.read(name)
    else:
        artifact = path / name
        if optional and not artifact.exists():
            return None
        if artifact.stat().st_size > limit:
            raise ValueError('saved artifact too large')
        raw = artifact.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('saved artifact must be an object: ' + name)
    return value


def load_saved(path):
    path = Path(path)
    task = _saved_json(path, 'task.json')
    evidence = _saved_json(path, 'evidence.json')
    if task.get('state') not in {'COMPLETED','FAILED','CANCELLED'}:
        raise ValueError('replay only a terminal saved task')
    if task['id'] != evidence['task_id'] or task['session_key'] != evidence['session_key']:
        raise ValueError('saved task/evidence identity mismatch')
    catalog=EvidenceCatalog(task['id'],task['session_key'])
    for row in evidence['items']:
        item=catalog.add(source=row['source'],raw=row['raw'],tool_call_id=row.get('tool_call_id'),metadata=row.get('metadata',{}))
        if item.ref!=row['ref']: raise ValueError('saved evidence ref changed while loading')
    return task,catalog


def load_report_context(path, task):
    """Keep the original time anchor, scope changes and non-success stop reasons.

    Replaying only Evidence must not erase that the investigation timed out.
    Missing legacy context is explicit, never reconstructed as successful work.
    """
    path = Path(path)
    session = _saved_json(path, 'session.json', optional=True)
    result = _saved_json(path, 'result.json', optional=True)
    controls = f"原请求/计划时刻：{task.get('scheduled_for') or task.get('created_at') or '未记录'}\n"
    if session is not None:
        if session.get('task_id') != task['id'] or session.get('session_key') != task['session_key']:
            raise ValueError('saved session identity mismatch')
        turns = session.get('turns', [])
        if not isinstance(turns, list) or any(not isinstance(turn, dict) for turn in turns):
            raise ValueError('invalid saved session turns')
        relevant = [turn for turn in turns if turn.get('kind') in ('STEER', 'RESUME')]
        if any(not isinstance(turn.get('content'), str) for turn in relevant):
            raise ValueError('invalid saved control content')
        controls += '\n'.join(f"{turn['kind']}: {turn['content']}" for turn in relevant)
    else:
        controls += '历史控制记录未提供；不能假定已恢复完整上下文。'
    reasons = (result or {}).get('investigation_reasons')
    if reasons is None:
        reason = task.get('last_reason') or 'saved_execution_outcome_unknown'
        reasons = [reason]
    if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
        raise ValueError('invalid saved completion reasons')
    return controls, tuple(reasons)


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--saved',type=Path,required=True,help='review ZIP or terminal task directory')
    ap.add_argument('--out',type=Path,required=True,help='new independent output directory')
    ap.add_argument('--model',required=True)
    ap.add_argument('--base-url',default='http://127.0.0.1:18002/v1')
    ap.add_argument('--data-bind',action='append',default=[],help='explicit HOST:AGENT:ro for original image evidence')
    ap.add_argument('--execute',action='store_true',help='authorize one no-tool model request')
    args=ap.parse_args(argv)
    if not args.execute: ap.error('--execute is required for a model request')
    task,catalog=load_saved(args.saved)
    controls,reasons=load_report_context(args.saved,task)
    out=args.out.expanduser().resolve();saved=args.saved.expanduser().resolve()
    if saved.is_dir() and (out==saved or saved in out.parents):
        raise ValueError('output must be outside the original task directory')
    os.umask(0o077);out.mkdir(parents=True,exist_ok=False)
    composer=TextReportComposer(StreamingFinalizerClient(args.base_url,api_key=os.environ.get('SCOPEX_API_KEY',''),timeout_s=180),
                                model=args.model,media_loader=EvidenceMediaLoader(tuple(args.data_bind)))
    result=composer.run(user_request=task['user_request'],catalog=catalog,
                        control_context=controls,completion_reasons=reasons)
    payload={'source_task_id':task['id'],'source_task_state':task['state'],
             'replayed_at':datetime.now(timezone.utc).isoformat(),
             'tool_calls':0,'valid':result.valid,'report_text':result.text,'report_meta':result.meta,
             'note':'Saved Evidence only. Does not repeat business calculation, grade accuracy or modify old results.'}
    (out/'result.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    (out/'report.md').write_text(result.text)
    print(json.dumps({'output':str(out),'status':result.status,'errors':result.meta['errors']},ensure_ascii=False))
    return 0 if result.valid else 2


if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,KeyError,zipfile.BadZipFile) as exc:
        print(type(exc).__name__+': '+str(exc),file=sys.stderr);raise SystemExit(2)
