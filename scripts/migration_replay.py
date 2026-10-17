"""Host-side explicit acceptance. Only the generated private lab may be used.

Docker tools own ZenMoney credentials; this controller uses the public backend
HTTP contract just like a collector. It is not a claim of Chrome/HTTPS UI QA.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from uuid import UUID, uuid4

from migration_lab import read_env


def save(path,data):
    temporary=path.with_suffix('.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(data,stream,indent=2);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


class Replay:
    def __init__(self,env_file):
        self.root=Path(__file__).resolve().parents[1]
        self.env_file=Path(env_file).resolve();self.env=read_env(self.env_file)
        self.state=Path(self.env['LAB_STATE_DIR']).resolve()
        run=self.env['LAB_RUN_ID']
        if (str(UUID(run))!=run or self.state!=self.env_file.parent or
                self.state!=self.root/'.local/migration'/run or
                len({self.env['LAB_PORT_A'],self.env['LAB_PORT_B']})!=2 or
                '18792' in (self.env['LAB_PORT_A'],self.env['LAB_PORT_B'])):
            raise RuntimeError('Only a generated isolated lab configuration is allowed')
        self.project='zenimport-lab-'+run
        self.path=self.state/'replay.json'
        self.journal=json.loads(self.path.read_text()) if self.path.exists() else {'run':run,'completed':{},'commands':{}}
        if self.journal['run']!=run:raise RuntimeError('Wrong replay journal')
        self.child_env={k:v for k,v in os.environ.items() if not k.startswith('LAB_')}

    def dc(self,*args):
        result=subprocess.run(['docker','compose','--env-file',str(self.env_file),'-p',self.project,
            '-f',str(self.root/'compose.migration-lab.yaml'),*args],cwd=self.root,env=self.child_env,
            capture_output=True,text=True)
        # Raw command logs stay private, never show headers/config values.
        with (self.state/'replay-docker.log').open('a') as stream:
            os.chmod(stream.name,0o600);stream.write(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError('Lab Docker action failed; inspect private replay-docker.log')
        return result.stdout

    def tool(self,*args):
        output=self.dc('--profile','tools','run','--rm','--no-deps','tools',*args)
        return json.loads(output.strip().splitlines()[-1])

    def ensure_server(self,instance):
        other='b' if instance=='a' else 'a'
        self.dc('stop','server-'+other)
        self.dc('--profile',instance,'up','-d','--no-build','--wait','server-'+instance)

    def http(self,instance,path,body=None,method=None):
        request=urllib.request.Request('http://127.0.0.1:'+self.env['LAB_PORT_'+instance.upper()]+'/api/v1'+path,
            data=json.dumps(body).encode() if body is not None else None,method=method,
            headers={'Authorization':'Bearer '+self.env['LAB_API_TOKEN_'+instance.upper()],'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=100) as response:return json.load(response)
        except urllib.error.HTTPError as exc:
            code=json.loads(exc.read()).get('error',{}).get('code','unknown')
            raise RuntimeError(f'Backend HTTP {exc.code}: {code}') from None

    def history(self):return json.loads((self.state/'history.json').read_text())

    def audit(self,instance):
        path=self.state/('server-'+instance)/'sdk-audit.jsonl'
        return [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []

    def connection(self,instance,variant):
        plan=self.history()['variants'][variant];run=self.env['LAB_RUN_ID']
        items=[{'source_key':'lab:'+run+':'+variant+':'+str(i),'identity_scope':'bank',
            'label':'LAB '+variant+' '+str(i),'masked_number':'LAB'+str(i),
            'currency':'USD' if i==2 else 'VND','scale':2 if i==2 else 0,'supported':True}
            for i in range(3)]
        result=self.http(instance,'/connections/discover',{
            'bank':'vietinbank','profile_key':'lab:'+run+':'+variant,'profile_identity':'user_selected',
            'connector_version':'migration-lab-1','capabilities':{'timezone':'Asia/Ho_Chi_Minh',
                'currencies':['VND','USD'],'max_window_days':30,'details':'included'},
            'accounts_complete':True,'accounts':items})
        for account in result['accounts']:
            index=int(account['source_key'].rsplit(':',1)[1])
            self.http(instance,'/accounts/'+account['id']+'/settings',{
                'expected_version':account['settings_version'],'selection':'enabled',
                'zenmoney_account_id':plan['accounts'][index]['id'],'import_start':plan['import_start']},'PUT')
        return self.http(instance,'/connections/'+result['id'])

    def capture(self,label,instance,variant,*,later=False,expected_new=0):
        if label in self.journal['completed']:return
        self.ensure_server(instance)
        plan=self.history()['variants'][variant]
        connection=self.connection(instance,variant)
        command=self.journal['commands'].setdefault(label,{'request_id':str(uuid4()),'worker_id':str(uuid4()),
            'audit_before':len(self.audit(instance))})
        save(self.path,self.journal)
        body={'connection_id':connection['id'],'request_id':command['request_id'],'reimport_from':plan['import_start']}
        job=self.http(instance,'/jobs',body)
        if job['capture_state']=='paused':
            command.setdefault('resume_request_id',str(uuid4()));save(self.path,self.journal)
            job=self.http(instance,'/jobs',body|{'request_id':command['resume_request_id']})
        if job['capture_state']!='complete':
            for progress in job['accounts']:
                account=next(a for a in connection['accounts'] if a['id']==progress['account_id'])
                index=int(account['source_key'].rsplit(':',1)[1]);target=plan['accounts'][index]['id']
                for interval in progress['intervals']:
                    if interval['state']=='complete':continue
                    lease=self.http(instance,'/jobs/'+job['id']+'/lease',{'worker_id':command['worker_id']})
                    auth={'worker_id':command['worker_id'],'lease_epoch':lease['epoch']}
                    records=[x['record'] for x in plan['records'] if x['target']==target
                        and (later or x['phase']=='initial')
                        and interval['period']['start']<=x['record']['local_date']<=interval['period']['end']]
                    route='/jobs/'+job['id']+'/intervals/'+interval['id']
                    self.http(instance,route+'/pages/1',auth|{'page_number':1,'total_pages':1,
                        'total_records':len(records),'records':records},'PUT')
                    self.http(instance,route+'/complete',auth|{'total_pages':1,'total_records':len(records)})
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            result=self.http(instance,'/jobs/'+job['id'])
            if result['capture_state']=='complete' and result['delivery_state']=='complete':break
            # Intent marks bank processing uncertain while the HTTP exchange is
            # still in flight. Only a settled blocked state is an unknown result.
            if result['delivery_state']!='running':
                if result['delivery']['uncertain']:raise RuntimeError('Unknown delivery outcome; no write retry')
                if result['errors']:raise RuntimeError('Delivery blocked: '+','.join(e['code'] for e in result['errors']))
            time.sleep(1)
        else:raise RuntimeError('Delivery verification deadline exceeded')
        events=self.audit(instance)[command['audit_before']:]
        ids=[identifier for event in events for identifier in event['entities'].get('transaction',[])]
        if len(ids)!=expected_new or len(set(ids))!=len(ids):
            raise RuntimeError(f'{label}: expected {expected_new} created IDs, observed {len(ids)}')
        summary={'stage':label,'new_transaction_requests':len(events),'new_transaction_ids':len(ids),
            'capture_state':result['capture_state'],'delivery_state':result['delivery_state']}
        self.journal['completed'][label]=summary;save(self.path,self.journal)
        print(json.dumps(summary),flush=True)

    def once(self,label,action):
        if label in self.journal['completed']:return
        result=action()
        self.journal['completed'][label]=result if isinstance(result,dict) else {'complete':True}
        save(self.path,self.journal)
        print(json.dumps({'stage':label,'complete':True}),flush=True)

    def verify(self,label,variant,instance,*,later=False,edited=False):
        args=['verify','--variant',variant,'--instance',instance]
        if later:args.append('--later')
        if edited:args.append('--edited')
        self.once(label,lambda:self.tool(*args))

    def archive_b(self,label):
        self.dc('stop','server-b');self.dc('rm','-f','server-b')
        source=self.state/'server-b';archive=self.state/('server-b-'+label)
        if not archive.exists():source.rename(archive)
        source.mkdir(exist_ok=True,mode=0o700)

    def restore(self):
        self.dc('stop','server-a','server-b')
        target=self.state/'server-b/zenimport.sqlite3'
        if target.exists():
            # The no-clobber snapshot may have completed before checkpoint ACK.
            def instance(file):
                with sqlite3.connect(file.as_uri()+'?mode=ro',uri=True) as db:
                    return db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
            if instance(target)!=instance(self.state/'server-a/zenimport.sqlite3'):
                raise RuntimeError('Restore destination already contains another database')
            return
        self.dc('--profile','tools','run','--rm','--no-deps','--entrypoint','python','tools',
            '-m','backend.snapshot','export','--source','/lab/server-a/zenimport.sqlite3',
            '--destination','/lab/server-b/zenimport.sqlite3')

    def run(self):
        self.once('seed-history',lambda:self.tool('history','--write'))
        self.capture('migrated-a-initial','a','migrated',expected_new=2)
        self.verify('migrated-a-initial-verified','migrated','a')
        self.capture('migrated-a-repeat','a','migrated')
        self.once('mobile-changes',lambda:self.tool('mutate','--write'))
        def refresh():
            self.ensure_server('a');connection=self.connection('a','migrated')
            self.http('a','/connections/'+connection['id']+'/discrepancies?refresh=true')
        self.once('mobile-mirror-refreshed',refresh)
        self.once('archive-b-before-copy',lambda:self.archive_b('initial'))
        self.once('restore-a-to-b',self.restore)
        self.capture('migrated-b-copy','b','migrated')
        self.verify('migrated-b-copy-verified','migrated','b',edited=True)
        self.capture('migrated-b-repeat','b','migrated')
        self.capture('migrated-b-new','b','migrated',later=True,expected_new=2)
        self.verify('migrated-b-new-verified','migrated','b',later=True,edited=True)
        self.capture('migrated-b-new-repeat','b','migrated',later=True)
        self.capture('empty-a-initial','a','empty',expected_new=2)
        self.verify('empty-a-initial-verified','empty','a')
        self.capture('empty-a-repeat','a','empty')
        self.once('archive-b-before-empty',lambda:self.archive_b('migrated'))
        self.capture('empty-b-first','b','empty')
        self.verify('empty-b-first-verified','empty','b')
        copied=self.journal['completed']['migrated-b-copy-verified']['instance_id']
        fresh=self.journal['completed']['empty-b-first-verified']['instance_id']
        original=self.journal['completed']['migrated-a-initial-verified']['instance_id']
        if copied!=original:raise RuntimeError('Copied database lost its source instance identity')
        if copied==fresh:raise RuntimeError('Empty database was not actually fresh')
        self.capture('empty-b-repeat','b','empty')
        self.capture('empty-b-new','b','empty',later=True,expected_new=2)
        self.verify('empty-b-new-verified','empty','b',later=True)
        self.capture('empty-b-new-repeat','b','empty',later=True)
        self.journal['result']='verified';save(self.path,self.journal)
        print(json.dumps({'result':'verified','scenarios':2,'financial_jobs':12,'unexpected_creates':0}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    if not args.write:parser.error('Explicit --write is required for test-account financial acceptance')
    replay=Replay(args.env_file)
    with (replay.state/'.replay.lock').open('a') as lock:
        os.chmod(lock.name,0o600)
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another replay controller is running') from None
        try:replay.run()
        finally:replay.dc('stop','server-a','server-b')


if __name__=='__main__':main()
