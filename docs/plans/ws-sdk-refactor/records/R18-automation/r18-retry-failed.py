import json,os,re,shlex,signal,subprocess,time
from pathlib import Path
root=Path('/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk')
e=root/'docs/plans/ws-sdk-refactor/records/R18-automation'
orphans=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit():continue
    try:
        cwd=str((p/'cwd').resolve());args=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode()
        if cwd.startswith('/tmp/r18-matrix/') and args.startswith('node '):
            orphans.append({'pid':int(p.name),'cwd':cwd,'args':args});os.kill(int(p.name),signal.SIGTERM)
    except (OSError,UnicodeError):pass
(e/'orphan-node-cleanup.json').write_text(json.dumps(orphans,indent=2)+'\n')
results=[]
for v in ['311','312','313','314']:
    failures=re.findall(r'^FAILED (\S+)',(e/f'pytest-py{v}.log').read_text(),re.M)
    args=[f'/tmp/r18-envs/py{v}/bin/python','-m','pytest','-p','no:cacheprovider','-q',*failures]
    env=os.environ.copy();env.update(PYTHONPATH='',MCBE_REQUIRE_CROSS_LANGUAGE='1',COVERAGE_FILE=str(e/f'.coverage-retry-py{v}'))
    with (e/'commands-retry.log').open('a') as log:log.write(f'cwd=/tmp/r18-matrix/py{v} PYTHONPATH=\'\' MCBE_REQUIRE_CROSS_LANGUAGE=1 COVERAGE_FILE={env["COVERAGE_FILE"]} {shlex.join(args)}\n')
    start=time.monotonic()
    with (e/f'retry-failed-py{v}.log').open('w') as log:p=subprocess.run(args,cwd=f'/tmp/r18-matrix/py{v}',env=env,stdout=log,stderr=subprocess.STDOUT)
    item=dict(python=v,failed_nodes=failures,exit_code=p.returncode,seconds=round(time.monotonic()-start,2));results.append(item);(e/'retry-results.json').write_text(json.dumps(results,indent=2)+'\n');print(item,flush=True)
