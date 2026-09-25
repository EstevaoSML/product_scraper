"""Execute the user's existing agent serially and import its saved evidence."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
if __package__:
    from .retail_catalog import ROOT, RetailCatalog, import_report, record_run
else:
    from retail_catalog import ROOT, RetailCatalog, import_report, record_run


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--agent-root',type=Path,required=True)
    parser.add_argument('--agent-python',default=sys.executable,help='Python with the agent dependencies; defaults to the Python running this collector')
    parser.add_argument('--openai-key-file',type=Path)
    parser.add_argument('--image-max-cost-usd',default='0')
    parser.add_argument('--research-max-cost-usd',default='0.05')
    parser.add_argument('--ids',nargs='*')
    parser.add_argument('--batch',default=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),help='Use the same batch name to resume without repeating completed paid calls')
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',args.batch):
        raise SystemExit('Nome de lote inválido.')
    agent=args.agent_root.resolve()
    python=args.agent_python
    env=os.environ.copy()
    env.pop('PYTHONPATH',None)
    env['PYTHONDONTWRITEBYTECODE']='1'
    if args.openai_key_file:
        env['OPENAI_API_KEY']=args.openai_key_file.read_text(encoding='utf-8-sig').strip()
    repository=RetailCatalog()
    manifest=json.loads((ROOT/'data'/'products.json').read_text(encoding='utf-8'))
    chosen=[p for p in manifest if args.ids is None or p['id'] in args.ids]
    if not chosen or (args.ids and set(args.ids)-{p['id'] for p in chosen}):
        raise SystemExit('IDs de produto inválidos.')
    lock=ROOT/'data'/'collection.lock'
    try: fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise SystemExit('Já existe uma coleta. Verifique o processo antes de remover collection.lock.')
    os.close(fd)
    try:
        for seed in chosen:
            directory=ROOT/'data'/'reports'/args.batch/seed['id']
            directory.mkdir(parents=True,exist_ok=True)
            # Resume only IDs with no diagnostic result. A new paid run requires a new invocation/explicit cleanup.
            if list(directory.glob('diagnostics-*.json')):
                print(json.dumps({'id':seed['id'],'status':'already_attempted'}),flush=True)
                continue
            command=[str(python),'-m','app.research_job','--url','https://www.kabum.com.br/',
                '--product',seed['query'],'--max-cost-usd',args.research_max_cost_usd,'--image-max-cost-usd',args.image_max_cost_usd,
                '--key-file',str(agent/'secrets'/'api_key.txt'),'--output-dir',str(directory),
                '--scrape-output-dir',str(directory/'scrapes')]
            print(json.dumps({'id':seed['id'],'status':'started'},ensure_ascii=False),flush=True)
            record_run(repository.db_path,seed['id'],'running')
            try:
                process=subprocess.run(command,cwd=agent,env=env,capture_output=True,text=True,timeout=290,encoding='utf-8',errors='replace')
            except subprocess.TimeoutExpired:
                record_run(repository.db_path,seed['id'],'failed','Tempo limite do processo')
                print(json.dumps({'id':seed['id'],'status':'timeout'}),flush=True)
                break
            diagnostics=sorted(directory.glob('diagnostics-*.json'))
            reports=sorted(directory.glob('research-*.json'))
            if process.returncode or not diagnostics or not reports:
                record_run(repository.db_path,seed['id'],'failed','Falha do agente; verifique MCP, credenciais e dependências.')
                print(json.dumps({'id':seed['id'],'status':'failed','reason':'agent_process_failed'}),flush=True)
                break
            metadata=json.loads(diagnostics[-1].read_text(encoding='utf-8'))
            report=json.loads(reports[-1].read_text(encoding='utf-8'))
            imported=import_report(repository.db_path,seed['id'],report,reports[-1].relative_to(ROOT))
            image=metadata.get('image') or {}
            local_image=None
            if image.get('status')=='generated' and image.get('file'):
                source=Path(image['file']).resolve()
                if not source.is_relative_to(directory.resolve()):
                    raise ValueError('Image outside report directory')
                if source.read_bytes()[:8]!=b'\x89PNG\r\n\x1a\n':
                    raise ValueError('Invalid PNG')
                dest=ROOT/'static'/'products'/f"{seed['id']}.png"
                dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,dest)
                local_image=f'/static/products/{seed["id"]}.png'
            record_run(repository.db_path,seed['id'],report['status'],report.get('reason'),local_image,image.get('status','disabled'))
            print(json.dumps({'id':seed['id'],'status':report['status'],'imported':imported,
                'price_present':(report.get('product') or {}).get('price') is not None,
                'research_cost':metadata.get('budgets',{}).get('usd_accounted'),
                'image_status':image.get('status'),'image_reason':image.get('reason'),
                'image_cost':image.get('usd_accounted')},ensure_ascii=False),flush=True)
            if report['status']=='blocked' or image.get('budget_exceeded'):
                break
    finally:
        lock.unlink(missing_ok=True)


if __name__=='__main__':
    main()
