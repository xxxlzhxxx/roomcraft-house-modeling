"""Run a resumable Seed3D image-to-GLB experiment without persisting credentials."""
import argparse
import base64
import json
import os
import re
import time
import urllib.request
import urllib.error
import zipfile
import io
from pathlib import Path


def config():
    model = os.getenv('ARK_DEFAULT_3D_MODEL') or os.getenv('ARK_SEED3D_MODEL')
    key = os.getenv('ARK_SEED3D_API_KEY')
    lines = Path(os.getenv('LLM_INVENTORY_FILE', str(Path.home()/'WorkSpace/LLM_env.md'))).read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if re.search(r'seed[ _.-]?3d', line, re.I))
    match = re.search(r'ep-[\w-]+', '\n'.join(lines[index+1:index+5]))
    keys = re.findall(r'(?im)API\s*key\s*[:：]\s*([^\s`]+)', '\n'.join(lines[:index]))
    if not model and match: model = match.group(0)
    if not key and keys: key = keys[-1]
    if not model or not key: raise RuntimeError('Seed3D private configuration is incomplete')
    return model,key


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--image',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--wait',type=int,default=0)
    args=p.parse_args()
    model,key=config()
    base=os.getenv('ARK_BASE_URL','https://ark.cn-beijing.volces.com/api/v3').rstrip('/')
    def request(path,body=None):
        req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=90) as res: return json.load(res)
        except urllib.error.HTTPError as e:
            try: data=json.loads(e.read());err=data.get('error',{})
            except Exception: err={}
            # Do not output provider messages: they can echo private endpoint IDs.
            raise RuntimeError('Seed3D HTTP '+str(e.code)+' code='+str(err.get('code','unknown'))) from None
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    state_path=out/'task.json'
    if state_path.exists(): state=json.loads(state_path.read_text())
    else:
        mime='image/png' if args.image.suffix.lower()=='.png' else 'image/jpeg'
        uri='data:'+mime+';base64,'+base64.b64encode(args.image.read_bytes()).decode()
        result=request('/contents/generations/tasks',{'model':model,'content':[
            {'type':'text','text':'--subdivisionlevel low --fileformat glb'},
            {'type':'image_url','image_url':{'url':uri}}]})
        state={'id':result['id'],'status':'submitted','input':args.image.name,'requestedFormat':'glb','requestedDetail':'low','submittedAt':time.time()}
        state_path.write_text(json.dumps(state,indent=2))
        print('Seed3D task accepted',flush=True)
    deadline=time.monotonic()+args.wait
    while True:
        result=request('/contents/generations/tasks/'+state['id'])
        state.update(status=result['status'],checkedAt=time.time())
        if result.get('usage'): state['usage']=result['usage']
        if result.get('error'): state['errorCode']=result['error'].get('code','unknown')
        # Public model name is useful evidence; never persist a private endpoint identifier.
        if result.get('model','').startswith('doubao-'): state['resolvedModel']=result['model']
        state_path.write_text(json.dumps(state,indent=2))
        print('Seed3D status:',state['status'],flush=True)
        if state['status']=='succeeded':
            url=result.get('content',{}).get('file_url')
            if not url: raise RuntimeError('Succeeded task has no artifact URL')
            with urllib.request.urlopen(url,timeout=120) as response: binary=response.read()
            if binary[:4]==b'glTF': (out/'asset.glb').write_bytes(binary)
            else:
                archive=zipfile.ZipFile(io.BytesIO(binary))
                files=[n for n in archive.namelist() if n.lower().endswith('.glb')]
                if len(files)!=1: raise RuntimeError('Expected exactly one GLB in generated archive')
                (out/'asset.glb').write_bytes(archive.read(files[0]))
            print('Saved GLB, bytes:',(out/'asset.glb').stat().st_size,flush=True)
            break
        if state['status'] in {'failed','cancelled','expired'} or time.monotonic()>=deadline: break
        time.sleep(30)


if __name__=='__main__':
    try: main()
    except Exception as e:
        if isinstance(e,RuntimeError): print(str(e),flush=True)
        else: print('Seed3D request failed:',type(e).__name__,flush=True)
        raise SystemExit(1)
