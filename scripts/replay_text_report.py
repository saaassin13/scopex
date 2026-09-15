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


def load_saved(path):
    path=Path(path)
    if path.is_file():
        with zipfile.ZipFile(path) as z:
            def read(name):
                if z.getinfo(name).file_size>8*1024*1024: raise ValueError('saved artifact too large')
                return json.loads(z.read(name))
            task,evidence=read('task.json'),read('evidence.json')
    else:
        task=json.loads((path/'task.json').read_text())
        evidence=json.loads((path/'evidence.json').read_text())
    if task.get('state') not in {'COMPLETED','FAILED','CANCELLED'}:
        raise ValueError('replay only a terminal saved task')
    if task['id'] != evidence['task_id'] or task['session_key'] != evidence['session_key']:
        raise ValueError('saved task/evidence identity mismatch')
    catalog=EvidenceCatalog(task['id'],task['session_key'])
    for row in evidence['items']:
        item=catalog.add(source=row['source'],raw=row['raw'],tool_call_id=row.get('tool_call_id'),metadata=row.get('metadata',{}))
        if item.ref!=row['ref']: raise ValueError('saved evidence ref changed while loading')
    return task,catalog


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
    out=args.out.expanduser().resolve();saved=args.saved.expanduser().resolve()
    if saved.is_dir() and (out==saved or saved in out.parents):
        raise ValueError('output must be outside the original task directory')
    os.umask(0o077);out.mkdir(parents=True,exist_ok=False)
    composer=TextReportComposer(StreamingFinalizerClient(args.base_url,api_key=os.environ.get('SCOPEX_API_KEY',''),timeout_s=180),
                                model=args.model,media_loader=EvidenceMediaLoader(tuple(args.data_bind)))
    result=composer.run(user_request=task['user_request'],catalog=catalog)
    payload={'source_task_id':task['id'],'replayed_at':datetime.now(timezone.utc).isoformat(),
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
